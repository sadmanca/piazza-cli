import os
import sys
import json
import getpass
from piazza_api import Piazza
from cmd import Cmd
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
import questionary
from rich import box
from rich.align import Align
from rich.console import Group
from rich.live import Live
import keyboard
from rich.markdown import Markdown
import re
import time # Added for caching timestamp

CRED_FILE = os.path.expanduser("~/.piazza_cli_creds.json")
console = Console()

try:
    import html2text
    _html2md = html2text.HTML2Text()
    _html2md.ignore_links = False
    _html2md.ignore_images = False
    _html2md.body_width = 0
    def html_to_markdown(html):
        return _html2md.handle(html)
except ImportError:
    # Fallback: strip tags, no formatting
    def html_to_markdown(html):
        return re.sub('<[^<]+?>', '', html or '')

# Imports for semantic search
try:
    from sentence_transformers import SentenceTransformer, util
    import torch
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENTENCE_TRANSFORMERS_AVAILABLE = False
    # We'll handle the absence of the library in the search method

# Global cache for posts and embeddings
POST_CACHE = {}
CACHE_EXPIRY_SECONDS = 3600 # 1 hour

class PiazzaCLI(Cmd):
    intro = "Welcome to Piazza CLI. Type help or ? to list commands."
    prompt = "piazza> "

    def __init__(self):
        super().__init__()
        self.piazza = Piazza()
        self.logged_in = False
        self.courses = []
        self.current_course = None
        self.sentence_model = None # Initialize sentence model
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                # Load a pre-trained model. This might take a moment on first run.
                # Consider moving this to a point where it's clear semantic search will be used,
                # or provide feedback to the user if it's slow.
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress:
                    progress.add_task(description="Loading semantic search model (all-mpnet-base-v2, first time may be slow)...", total=None) # Updated description
                    self.sentence_model = SentenceTransformer('all-mpnet-base-v2') # Changed model
                console.print("[dim]Semantic search model (all-mpnet-base-v2) loaded.[/dim]") # Updated message
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load sentence transformer model: {e}[/yellow]")
                console.print("[yellow]Semantic search will fall back to keyword matching.[/yellow]")
                self.sentence_model = None
        else:
            console.print("[yellow]Warning: 'sentence-transformers' library not found. Semantic search will fall back to keyword matching.[/yellow]")
            console.print("[yellow]Install it with: pip install sentence-transformers torch[/yellow]")

        creds = self._load_creds()
        if creds:
            try:
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                    progress.add_task(description="Logging in with cached credentials...", total=None)
                    self.piazza.user_login(email=creds['email'], password=creds['password'])
                self.logged_in = True
                console.print(f"[green]Logged in as {creds['email']}[/green]")
                self.do_courses("")
                return
            except Exception as e:
                console.print("[yellow]Cached credentials failed. Please login again.[/yellow]")
        self._main_menu()

    def _main_menu(self):
        while not self.logged_in:
            console.print(Panel(Text("Piazza CLI Main Menu", style="bold white on blue"), expand=False, border_style="bright_blue"))
            console.print(f"[bold magenta]1[/bold magenta] [cyan]Login[/cyan]")
            console.print(f"[bold magenta]2[/bold magenta] [red]Exit[/red]")
            choice = input("Select an option [1/2]: ").strip()
            if choice == "1":
                self._login()
            elif choice == "2":
                console.print(Text("Goodbye!", style="bold red"))
                sys.exit(0)

    def _login(self):
        creds = self._load_creds()
        if creds:
            try:
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                    progress.add_task(description="Logging in with cached credentials...", total=None)
                    self.piazza.user_login(email=creds['email'], password=creds['password'])
                self.logged_in = True
                console.print(f"[green]Logged in as {creds['email']}[/green]")
                return
            except Exception as e:
                console.print("[yellow]Cached credentials failed. Please login again.[/yellow]")
        # Prompt for login
        email = input("Email: ").strip()
        password = getpass.getpass("Password: ")
        try:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                progress.add_task(description="Logging in...", total=None)
                self.piazza.user_login(email=email, password=password)
            self.logged_in = True
            self._save_creds(email, password)
            console.print(f"[green]Logged in as {email}[/green]")
        except Exception as e:
            console.print(f"[bold red]Login failed:[/bold red] {e}")
            sys.exit(1)

    def _save_creds(self, email, password):
        with open(CRED_FILE, 'w') as f:
            json.dump({'email': email, 'password': password}, f)

    def _load_creds(self):
        if os.path.exists(CRED_FILE):
            try:
                with open(CRED_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def do_help(self, arg):
        """List available commands with descriptions."""
        commands = []
        for name in dir(self):
            if name.startswith('do_') and not name.startswith('do__') and name != 'do_EOF':
                method = getattr(self, name)
                doc = method.__doc__
                cmd_name = name[3:]  # strip 'do_'
                if doc:
                    commands.append((cmd_name, doc.strip()))
        table = Table(title="Piazza CLI Commands", header_style="bold bright_blue", row_styles=["","dim"], box=box.SIMPLE_HEAVY)
        table.add_column("Command", style="magenta bold", justify="left")
        table.add_column("Description", style="white")
        for cmd_name, doc in commands:
            table.add_row(cmd_name, doc)
        console.print(Panel(table, border_style="bright_blue", title="Commands", title_align="left"))

    def cmdloop(self, intro=None):
        # Override to always show courses as the main menu
        while True:
            if not self.logged_in:
                self._main_menu()
                continue
            exit_flag = self.do_courses("")
            if exit_flag == 'exit':
                self.do_exit("")
                break
            elif exit_flag == 'logout':
                self.do_logout("")
                break
            elif exit_flag == 'help':
                self.do_help("")
                continue
            # Otherwise, loop back to courses

    def do_courses(self, arg):
        """Interactively select a course and view questions (arrow keys, Enter to select). Press [q] to exit, [h] for help, [l] to logout, [s] to search, [a] for alt search."""
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
            progress.add_task(description="Fetching courses...", total=None)
            self.courses = self.piazza.get_user_classes()
        if not self.courses:
            console.print("[yellow]No courses found.[/yellow]")
            return
        # Sort by term (most recent first)
        def term_key(course):
            term_order = {'Winter': 3, 'Fall': 2, 'Summer': 1}
            parts = course['term'].split()
            if len(parts) == 2 and parts[0] in term_order:
                return (int(parts[1]), term_order[parts[0]])
            return (0, 0)
        sorted_courses = sorted(self.courses, key=term_key, reverse=True)
        PAGE_SIZE = 30
        page = 0
        total_pages = (len(sorted_courses) + PAGE_SIZE - 1) // PAGE_SIZE
        while True:
            start = page * PAGE_SIZE
            end = start + PAGE_SIZE
            page_courses = sorted_courses[start:end]
            course_labels = [f"{c['name']} [{c['term']}]" for c in page_courses]
            # Add navigation and utility options
            if total_pages > 1 and page < total_pages - 1:
                course_labels.append("[n] Next Page")
            if total_pages > 1 and page > 0:
                course_labels.append("[p] Previous Page")
            course_labels.append("[s] Search")
            course_labels.append("[q] Quit")
            course_labels.append("[h] Help")
            course_labels.append("[l] Logout")
            course_labels.append("[a] Semantic Search") # New option for semantic search
            # Remove shortcut_key_map, just use use_shortcuts=True
            choice = questionary.select(
                "Select a Course:",
                choices=course_labels,
                qmark="[piazza]",
                use_shortcuts=True
            ).ask()
            if choice is None:
                continue
            if choice == "[q] Quit":
                confirm = questionary.confirm("Are you sure you want to quit?", default=False).ask()
                if confirm:
                    sys.exit(0)
                else:
                    continue
            if choice == "[h] Help":
                self.do_help("")
                input("Press Enter to return to the course list...")
                continue
            if choice == "[l] Logout":
                confirm = questionary.confirm("Are you sure you want to logout?", default=False).ask()
                if confirm:
                    self.do_logout("")
                    sys.exit(0)
                else:
                    continue
            if choice == "[n] Next Page":
                page += 1
                continue
            if choice == "[p] Previous Page":
                page -= 1
                continue
            if choice == "[s] Search":
                # Prompt for course to search in
                if not page_courses:
                    continue
                course_search_labels = [f"{c['name']} [{c['term']}]" for c in page_courses]
                course_search_choice = questionary.select(
                    "Select a course to search in:",
                    choices=course_search_labels + ["Back"]
                ).ask()
                if course_search_choice == "Back" or course_search_choice is None:
                    continue
                selected_idx = course_search_labels.index(course_search_choice)
                selected_course = page_courses[selected_idx]
                self._search_in_course(selected_course)
                continue
            if choice == "[a] Semantic Search":
                # Prompt for course to search in
                if not page_courses:
                    continue
                course_search_labels = [f"{c['name']} [{c['term']}]" for c in page_courses]
                course_search_choice = questionary.select(
                    "Select a course for semantic search:",
                    choices=course_search_labels + ["Back"]
                ).ask()
                if course_search_choice == "Back" or course_search_choice is None:
                    continue
                selected_idx = course_search_labels.index(course_search_choice)
                selected_course = page_courses[selected_idx]
                self._alt_search_in_course(selected_course)
                continue
            if choice in course_labels:
                selected_idx = course_labels.index(choice)
                if selected_idx < len(page_courses):
                    self.current_course = page_courses[selected_idx]
                    console.print(Panel(f"Selected course: [bold green]{self.current_course['name']}[/bold green] ({self.current_course['term']})", style="bold white on dark_green", expand=False))
                    self._question_list_view()
            # Otherwise, loop again

    def _search_in_course(self, course):
        """Prompt for a search query and show results for the selected course."""
        net = self.piazza.network(course['nid'])
        query = questionary.text("Enter search query:").ask()
        if not query:
            return
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
            progress.add_task(description="Searching posts...", total=None)
            results = net.search_feed(query)
        # Handle both dict-with-feed and list return types
        if isinstance(results, dict) and 'feed' in results:
            posts = results['feed']
        elif isinstance(results, list):
            posts = results
        else:
            posts = []
        if not posts:
            console.print("[yellow]No results found.[/yellow]")
            input("Press Enter to return...")
            return
        post_labels = [f"[{p['nr']}] {p.get('subject', '')}" for p in posts]
        post_labels.append("Back")
        while True:
            choice = questionary.select(f"Search results in {course['name']}:", choices=post_labels).ask()
            if choice is None or choice == "Back":
                break
            idx = post_labels.index(choice)
            post_nr = posts[idx]['nr']
            self._show_post(post_nr, net)

    def _alt_search_in_course(self, course):
        """Prompt for a search query and show results using an semantic search method."""
        net = self.piazza.network(course['nid'])
        query = questionary.text("Enter search query for semantic (semantic) search:").ask()
        if not query:
            return

        course_nid = course['nid']
        cached_data = POST_CACHE.get(course_nid)
        all_posts = []
        post_embeddings = None

        # Check cache
        if cached_data and (time.time() - cached_data.get('timestamp', 0)) < CACHE_EXPIRY_SECONDS:
            console.print(f"[cyan]Using cached posts for {course['name']}...[/cyan]")
            all_posts = cached_data['posts']
            if self.sentence_model and 'embeddings' in cached_data:
                post_embeddings = cached_data['embeddings']
        
        if not all_posts:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress:
                fetch_task_id = progress.add_task(description=f"Fetching all posts for {course['name']}...", total=None) # Store TaskID
                offset = 0
                limit = 50 
                fetched_posts_count = 0
                while True:
                    try:
                        feed = net.get_feed(limit=limit, offset=offset)
                        if not feed or not feed.get('feed'):
                            break
                        current_batch = feed['feed']
                        all_posts.extend(current_batch)
                        fetched_posts_count += len(current_batch)
                        progress.update(fetch_task_id, description=f"Fetching all posts for {course['name']}... ({fetched_posts_count} fetched)") # Use TaskID
                        offset += limit
                        if len(current_batch) < limit:
                            break
                        time.sleep(0.1) # Small delay to be nice to the API
                    except Exception as e:
                        console.print(f"[red]Error fetching posts: {e}[/red]")
                        break
            
            if not all_posts:
                console.print("[yellow]No posts found in the course to search.[/yellow]")
                input("Press Enter to return...")
                return
            
            # Update cache with posts
            POST_CACHE[course_nid] = {'posts': all_posts, 'timestamp': time.time()}
            console.print(f"[cyan]Fetched and cached {len(all_posts)} posts for {course['name']}.[/cyan]")

        results = []
        if self.sentence_model:
            console.print(f"[cyan]Performing semantic search for '{query}' across {len(all_posts)} posts...[/cyan]")
            
            # Prepare texts for embedding
            # We need the full content for better semantic search, not just preview.
            # This requires fetching each post individually if not already done or if preview is insufficient.
            # For simplicity, we'll use subject + preview first, then enhance if needed.
            # A more robust solution would fetch full content for all_posts if not already detailed enough.
            
            post_texts_for_embedding = []
            posts_for_results = [] # Keep track of posts corresponding to texts

            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress:
                prepare_task_id = progress.add_task(description="Preparing post content for semantic search...", total=len(all_posts)) # Store TaskID
                for i, post_data in enumerate(all_posts):
                    # Fetch full post content if not already available or if only preview exists
                    # The feed usually contains 'subject' and 'preview' (a snippet of the content)
                    # For true semantic search, the full content is better.
                    # Let's assume 'history' contains the main content.
                    # This part might need adjustment based on how much detail `net.get_feed` provides.
                    # If `get_feed` provides enough content in `preview` or another field, use that.
                    # Otherwise, we might need to fetch each post individually here (can be slow).
                    
                    # For now, let's use subject and any available content snippet from the feed.
                    # If 'content' or 'history' is directly in post_data from the feed, use it.
                    # Piazza API's get_feed provides 'subject' and 'preview'.
                    # 'preview' is a dict with 'text' and 'html'.
                    
                    subject = post_data.get('subject', '')
                    content_preview_html = post_data.get('preview', {}).get('html', '')
                    content_preview_text = html_to_markdown(content_preview_html) if content_preview_html else ''
                    
                    # If we want full content, we'd need to do:
                    # full_post = net.get_post(post_data['id']) # or post_data['nr']
                    # main_content_html = full_post['history'][0]['content']
                    # main_content_text = html_to_markdown(main_content_html)
                    # combined_text = subject + "\\n\\n" + main_content_text
                    # For now, stick to preview to avoid many API calls unless cache is smarter
                    
                    combined_text = subject + "\\n\\n" + content_preview_text
                    if combined_text.strip(): # Only consider posts with some text
                        post_texts_for_embedding.append(combined_text)
                        posts_for_results.append(post_data) # Store the original post data
                    progress.update(prepare_task_id, advance=1, description=f"Preparing post content... ({i+1}/{len(all_posts)})") # Use TaskID

            if not post_texts_for_embedding:
                console.print("[yellow]No text content found in posts to perform semantic search.[/yellow]")
                input("Press Enter to return...")
                return

            # Generate embeddings for posts if not cached or cache is stale for embeddings
            if not post_embeddings or len(post_embeddings) != len(post_texts_for_embedding):
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress:
                    # Store TaskID
                    embeddings_task_id = progress.add_task(description=f"Generating embeddings for {len(post_texts_for_embedding)} posts...", total=None)
                    post_embeddings = self.sentence_model.encode(post_texts_for_embedding, convert_to_tensor=True, show_progress_bar=False)
                    # Potentially update progress here if encode had a callback, but it doesn't. Task is for the whole operation.
                # Update cache with embeddings
                if course_nid in POST_CACHE:
                     POST_CACHE[course_nid]['embeddings'] = post_embeddings
                     POST_CACHE[course_nid]['timestamp'] = time.time() # Update timestamp
                else: # Should not happen if posts were fetched
                     POST_CACHE[course_nid] = {'posts': posts_for_results, 'embeddings': post_embeddings, 'timestamp': time.time()}


            query_embedding = self.sentence_model.encode(query, convert_to_tensor=True)
            
            # util.semantic_search returns a list of lists, each inner list contains dicts with 'corpus_id' and 'score'
            search_hits = util.semantic_search(query_embedding, post_embeddings, top_k=10) # Get top 10 results
            
            # search_hits is a list (for the single query) of lists of hits
            for hit in search_hits[0]: 
                results.append(posts_for_results[hit['corpus_id']]) # Use original post data
        else:
            # Fallback to simple keyword search if sentence_model is not available
            console.print(f"[yellow]Semantic search model not available. Falling back to simple keyword search for '{query}'...[/yellow]")
            for post in all_posts:
                search_text = post.get('subject', '') + " " + post.get('preview', {}).get('text', '')
                if query.lower() in search_text.lower():
                    results.append(post)

        if not results:
            console.print(f"[yellow]No results found for '{query}' using semantic search.[/yellow]")
            input("Press Enter to return...")
            return

        post_labels = [f"[{p['nr']}] {p.get('subject', '')}" for p in results]
        post_labels.append("Back")

        while True:
            choice = questionary.select(f"Semantic search results in {course['name']}: ({len(results)} found)", choices=post_labels).ask()
            if choice is None or choice == "Back":
                break
            # Ensure choice is one of the post labels before trying to get index
            if choice in post_labels[:-1]: # Exclude "Back"
                idx = post_labels.index(choice)
                post_nr = results[idx]['nr']
                self._show_post(post_nr, net)
            elif choice == "Back":
                break
            # If choice is None (e.g., user pressed Esc), also break
            else: # Should not happen with questionary.select if choice is None
                break

    def _question_list_view(self):
        """Show a scrollable list of questions for the selected course. Enter to view discussion."""
        net = self.piazza.network(self.current_course['nid'])
        limit = 30
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
            progress.add_task(description="Fetching posts...", total=None)
            feed = net.get_feed(limit=limit)
        if not feed['feed']:
            console.print("[yellow]No posts found.[/yellow]")
            return
        posts = feed['feed']
        post_labels = [f"[{p['nr']}] {p.get('subject', '')}" for p in posts]
        post_labels.append("Back")
        while True:
            choice = questionary.select(f"Questions in {self.current_course['name']}:", choices=post_labels).ask()
            if choice is None or choice == "Back":
                break
            idx = post_labels.index(choice)
            post_nr = posts[idx]['nr']
            self._show_post(post_nr, net)

    def _show_post(self, post_nr, net):
        """Show the full discussion for a post, Reddit-style, with scrollable navigation and option to comment or go back."""
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
            progress.add_task(description=f"Fetching post {post_nr}...", total=None)
            post = net.get_post(int(post_nr))

        # Ensure html2text is imported if not already at the top of the file
        # from html2text import html2text as h2t_converter # Assuming it's available
        # html_to_markdown = lambda html: h2t_converter(html) if html else "" # User's existing lambda

        def render_entry(entry, indent=0, role=None):
            prefix = Text()
            if role == 'instructor':
                prefix.append("[INSTRUCTOR] ", style="bold blue")
            elif role == 'student':
                prefix.append("[STUDENT] ", style="bold green")
            elif role == 'op':
                prefix.append("[OP] ", style="bold magenta")
            elif role == 'followup':
                prefix.append("[FOLLOWUP] ", style="yellow")
            elif role == 'comment':
                prefix.append("[COMMENT] ", style="dim")
            
            from rich.padding import Padding # This was correctly placed in user's _show_post
            rendered_elements = []
            
            if str(prefix):
                rendered_elements.append(Padding(prefix, (0, 0, 0, indent * 4)))

            current_body_parts = []
            subject_text = entry.get('subject')
            if subject_text:
                current_body_parts.append(f"**{subject_text.strip()}**")

            html_content_str = entry.get('content', '')
            
            if html_content_str:
                processed_html_content = ""
                try:
                    # Directly use h2t_converter as html_to_markdown lambda implies
                    # Ensure h2t_converter is accessible in this scope (e.g. defined globally or passed)
                    # For this example, assuming html_to_markdown is the user's defined lambda using h2t_converter
                    raw_converted = html_to_markdown(html_content_str) 
                    if raw_converted is not None:
                        processed_html_content = str(raw_converted).strip()
                    else:
                        processed_html_content = "[Content conversion resulted in None]"
                except Exception as e:
                    # Consider logging the full exception 'e' for debugging purposes
                    processed_html_content = f"[Error converting content: {type(e).__name__}]"
                
                if processed_html_content: # Add if not empty
                    if current_body_parts: # If subject was already added, add separator
                        current_body_parts.append("\n\n" + processed_html_content)
                    else:
                        current_body_parts.append(processed_html_content)
            
            final_body_string = "".join(current_body_parts)

            if final_body_string.strip(): 
                md = Markdown(final_body_string)
                rendered_elements.append(Padding(md, (0, 0, 0, indent * 4)))
            
            return rendered_elements

        # Helper to recursively walk through children (replies/comments)
        def walk_children(children_data, current_indent):
            child_lines = []
            for child_item in children_data:
                # Assuming child_item is a dict representing a comment or nested reply
                child_lines.extend(render_entry(child_item, indent=current_indent, role='comment')) 
                if child_item.get('children'):
                    child_lines.extend(walk_children(child_item['children'], current_indent + 1))
            return child_lines

        # Main function to prepare all lines for the thread
        def walk_thread(current_post_data):
            output_lines = []
            # 1. Original Post
            output_lines.extend(render_entry(current_post_data, indent=0, role='op'))

            # 2. Process all direct children: student answers, instructor answers, and followups
            children_of_post = current_post_data.get('children', [])
            if children_of_post:
                for child_item in children_of_post:
                    item_type = child_item.get('type')
                    
                    if item_type == 's_answer':
                        output_lines.extend(render_entry(child_item, indent=1, role='student'))
                        if child_item.get('children'): # Comments on student answer
                            output_lines.extend(walk_children(child_item['children'], 2))
                    elif item_type == 'i_answer':
                        output_lines.extend(render_entry(child_item, indent=1, role='instructor'))
                        if child_item.get('children'): # Comments on instructor answer
                            output_lines.extend(walk_children(child_item['children'], 2))
                    elif item_type == 'followup':
                        output_lines.extend(render_entry(child_item, indent=1, role='followup'))
                        # Followups can have their own children (comments on the followup)
                        if child_item.get('children'): 
                            output_lines.extend(walk_children(child_item['children'], 2))
                    # Add elif for other child types if necessary in the future
            return output_lines

        thread_lines = walk_thread(post)
        # Flatten all lines for scrolling, regardless of entry
        window_size = 12 # Number of lines to show at once
        pos = 0 # Current top line index
        total_renderable_items = len(thread_lines)

        def render_window():
            # Ensure pos is within bounds
            nonlocal pos
            if total_renderable_items <= window_size:
                pos = 0 # No scrolling needed if content fits
                visible = thread_lines
            else:
                # Adjust pos if it's out of bounds after content change or window resize (though window_size is fixed here)
                if pos > total_renderable_items - window_size:
                    pos = total_renderable_items - window_size
                if pos < 0:
                    pos = 0
                visible = thread_lines[pos:pos+window_size]
            
            if not visible: # Handle case where thread_lines might be empty
                return Panel(Text("No content to display.", justify="center"), title=f"Post {post_nr}", border_style="cyan", expand=False)

            group = Group(*visible)
            # Update title to show scroll position if scrollable
            title_suffix = ""
            if total_renderable_items > window_size:
                title_suffix = f" (scroll {pos+1}-{min(pos+window_size, total_renderable_items)} of {total_renderable_items})"
            
            panel = Panel(group, title=f"Post {post_nr}{title_suffix} (Up/Down, c=comment, b=back)", border_style="cyan", expand=False)
            return Align.center(panel)

        with Live(render_window(), refresh_per_second=10, console=console, screen=True, transient=True) as live:
            while True:
                event = keyboard.read_event(suppress=True)
                if event.event_type == keyboard.KEY_DOWN:
                    if event.name == 'up':
                        if pos > 0:
                            pos -= 1
                            live.update(render_window())
                    elif event.name == 'down':
                        # Only allow scrolling down if there's more content below the current window
                        if total_renderable_items > window_size and pos < total_renderable_items - window_size:
                            pos += 1
                            live.update(render_window())
                    elif event.name == 'c':
                        # Temporarily stop live display to take input
                        live.stop()
                        console.print("[bold green]Enter your comment below. Press Enter to submit, Esc to cancel.[/bold green]")
                        # It's better to use questionary or handle Esc for cancellation properly
                        try:
                            comment_content = questionary.text("Comment:", qmark="✏️ ").ask()
                        except Exception: # Catch potential issues if questionary is interrupted
                            comment_content = None
                        
                        if comment_content: # If user submitted something (not None from Esc)
                            try:
                                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress_bar:
                                    progress_bar.add_task(description="Posting comment...", total=None)
                                    net.create_followup(post, comment_content) # Pass the post object or cid
                                console.print("[green]Comment posted. Refreshing post...[/green]")
                            except Exception as e:
                                console.print(f"[red]Failed to post comment:[/red] {e}")
                        else:
                            console.print("[yellow]Comment cancelled.[/yellow]")
                        # Restart live after input (or break to refresh post)
                        # For simplicity, we break to re-fetch and re-render the post with the new comment.
                        break 
                    elif event.name in ('b', 'escape', 'q'): # Added 'escape' and 'q'
                        break

    def do_help(self, arg):
        """List available commands with descriptions."""
        commands = []
        for name in dir(self):
            if name.startswith('do_') and not name.startswith('do__') and name != 'do_EOF':
                method = getattr(self, name)
                doc = method.__doc__
                cmd_name = name[3:]  # strip 'do_'
                if doc:
                    commands.append((cmd_name, doc.strip()))
        table = Table(title="Piazza CLI Commands", header_style="bold bright_blue", row_styles=["","dim"], box=box.SIMPLE_HEAVY)
        table.add_column("Command", style="magenta bold", justify="left")
        table.add_column("Description", style="white")
        for cmd_name, doc in commands:
            table.add_row(cmd_name, doc)
        console.print(Panel(table, border_style="bright_blue", title="Commands", title_align="left"))

    def do_logout(self, arg):
        """Logout and clear cached credentials"""
        if os.path.exists(CRED_FILE):
            os.remove(CRED_FILE)
        console.print("[yellow]Logged out. Restart the program to login again.[/yellow]")
        return True

    def do_exit(self, arg):
        """Exit the CLI"""
        console.print("[bold red]Goodbye![/bold red]")
        return True

    def do_EOF(self, arg):
        print()
        return self.do_exit(arg)

if __name__ == "__main__":
    PiazzaCLI().cmdloop()

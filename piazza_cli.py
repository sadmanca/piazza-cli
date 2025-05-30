import os
import sys
import json
import getpass
import time # Added for caching
try:
    from sentence_transformers import SentenceTransformer
    from sentence_transformers import util
    import torch # Often a dependency for sentence-transformers
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

import dateutil.parser # For parsing timestamps

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
            if post_embeddings is None or len(post_embeddings) != len(post_texts_for_embedding):
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
            search_hits = util.semantic_search(query_embedding, post_embeddings, top_k=30) # Get top 10 results
            
            # search_hits is a list (for the single query) of lists of hits
            for hit in search_hits[0]: 
                results.append(posts_for_results[hit['corpus_id']]) # Use original post data
        else:
            # Fallback to simple keyword search if sentence_model is not available
            console.print(f"[yellow]Semantic search model not available. Falling back to simple keyword search for '{query}'...[/yellow]")
            # In fallback, ensure 'results' contains posts with 'nr' and 'subject' at least
            temp_results = []
            for post_data in all_posts:
                search_text = post_data.get('subject', '') + " " + post_data.get('preview', {}).get('text', '')
                if query.lower() in search_text.lower():
                    temp_results.append(post_data)
            results = temp_results # Ensure results are consistently structured

        if not results:
            console.print(f"[yellow]No results found for '{query}'.[/yellow]")
            input("Press Enter to return...")
            return

        post_labels = []
        post_label_map = []  # Map from label index to result index
        SNIPPET_LENGTH = 70 # Target length for generic snippets from preview
        CONTEXT_WINDOW = 35 # Characters around query for contextual snippets
        MAX_DISPLAY_SNIPPET_LEN = 150 # Overall max length for a snippet line

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress_bar:
            task_id = progress_bar.add_task(description="Generating snippets for search results...", total=len(results))
            for i, p in enumerate(results):
                subject = p.get('subject', 'No Subject')
                post_nr = p.get('nr', 'N/A')
                snippet_text = "[Snippet not available]"
                try:
                    # Fetch full post details for snippet generation
                    full_post_details = net.get_post(p['nr']) 
                    main_content_html = ""
                    if full_post_details and full_post_details.get('history') and full_post_details['history']:
                        main_content_html = full_post_details['history'][0].get('content', '')

                    if main_content_html:
                        full_content_md = html_to_markdown(main_content_html).strip()
                        # Consolidate multiple newlines/spaces for easier processing and display
                        full_content_md_oneline = ' '.join(full_content_md.split())

                        if not full_content_md_oneline.strip():
                            snippet_text = "[Post content is empty or whitespace]"
                        else:
                            query_lower = query.lower()
                            content_lower = full_content_md_oneline.lower()
                            query_index = content_lower.find(query_lower)
                            
                            if query_index != -1: # Query found, create contextual snippet
                                start_idx = max(0, query_index - CONTEXT_WINDOW)
                                end_idx = min(len(full_content_md_oneline), query_index + len(query) + CONTEXT_WINDOW)
                                
                                temp_snip = full_content_md_oneline[start_idx:end_idx]
                                
                                prefix = "..." if start_idx > 0 else ""
                                suffix = "..." if end_idx < len(full_content_md_oneline) else ""
                                snippet_text = prefix + temp_snip.strip() + suffix
                            elif full_content_md_oneline: # Fallback to generic snippet from full content
                                snippet_text = full_content_md_oneline[:SNIPPET_LENGTH*2]
                                if len(full_content_md_oneline) > SNIPPET_LENGTH*2:
                                    snippet_text = snippet_text + "..."
                            # If full_content_md_oneline was empty, already handled
                    
                    # Fallback to original preview if full content processing didn't yield a good snippet
                    elif p.get('preview', {}).get('html', ''):
                        preview_html = p.get('preview', {}).get('html', '')
                        fallback_snippet_html = html_to_markdown(preview_html).strip()
                        fallback_snippet = ' '.join(fallback_snippet_html.split())
                        if fallback_snippet:
                            if len(fallback_snippet) > SNIPPET_LENGTH:
                                snippet_text = fallback_snippet[:SNIPPET_LENGTH] + "..."
                            else:
                                snippet_text = fallback_snippet
                        else:
                             snippet_text = "[no preview available (from feed)]"
                    else:
                        snippet_text = "[no preview or full content available]"

                except Exception: # Catchall for errors during get_post or snippet processing
                    # You might want to log the exception e for debugging
                    # For example: console.print(f"[dim red]Error generating snippet for post {post_nr}: {e}[/dim red]")
                    snippet_text = "[Error generating snippet]"
                
                # Ensure snippet_text is not excessively long for display
                if len(snippet_text) > MAX_DISPLAY_SNIPPET_LEN:
                    snippet_text = snippet_text[:MAX_DISPLAY_SNIPPET_LEN-3] + "..."

                # Two-line display: subject, then snippet (indented)
                post_labels.append(f"{i + 1}) {subject}")
                post_labels.append(f"        {snippet_text}")
                post_label_map.append(i)  # Only map the subject line to the result index
                progress_bar.update(task_id, advance=1)

        post_labels.append("Back")

        while True:
            choice = questionary.select(
                f"Semantic search results in {course['name']}: ({len(results)} found)",
                choices=post_labels
            ).ask()
            if choice is None or choice == "Back":
                break
            # Only allow selection on subject lines (odd indices)
            try:
                idx = post_labels.index(choice)
                if idx < len(post_label_map)*2 and idx % 2 == 0:
                    result_idx = post_label_map[idx // 2]
                    post_nr = results[result_idx]['nr']
                    self._show_post(post_nr, net)
            except Exception:
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
            elif role == 'note': # Added role for 'note' type posts
                prefix.append("[NOTE] ", style="bold cyan")
            
            from rich.padding import Padding 
            rendered_elements = []
            
            if str(prefix):
                rendered_elements.append(Padding(prefix, (0, 0, 0, indent * 4)))

            # 1. Author and Timestamp
            author_line_parts = []
            display_name = None
            created_ts = None 

            if role == 'op' and 'history' in entry and entry['history']:
                op_info = entry['history'][0]
                if op_info.get('anon') == 'no' and 'uid' in op_info: # uid might be name or need lookup
                    display_name = op_info['uid'] 
                elif op_info.get('anon', 'no') != 'no': 
                    display_name = f"Anonymous ({op_info.get('anon')})" if op_info.get('anon') else "Anonymous"
                created_ts = op_info.get('created')
            else: 
                if 'name' in entry: 
                    display_name = entry['name']
                elif 'anon_name' in entry and entry['anon_name'] and entry['anon_name'] != "Anonymous": 
                    display_name = entry['anon_name']
                elif 'uid' in entry and not display_name: 
                    if isinstance(entry['uid'], str) and '@' not in entry['uid'] and '.' not in entry['uid']:
                        display_name = entry['uid']
                if not display_name and entry.get('anon', 'no') != 'no':
                     display_name = f"Anonymous ({entry.get('anon')})" if entry.get('anon') else "Anonymous"
                elif not display_name: 
                    display_name = "User" 
                created_ts = entry.get('created')

            if display_name:
                author_line_parts.append(Text(str(display_name), style="italic dim"))

            if created_ts:
                try:
                    dt_obj = dateutil.parser.parse(created_ts)
                    author_line_parts.append(Text(f" ({dt_obj.strftime('%b %d, %Y, %I:%M %p')})", style="dim"))
                except Exception: 
                    author_line_parts.append(Text(f" ({created_ts})", style="dim"))
            
            if author_line_parts:
                author_text = Text.assemble(*author_line_parts)
                rendered_elements.append(Padding(author_text, (0, 0, 0, (indent * 4) + 2 )))

            # 2. Subject (Title for OP)
            subject_str = None
            if role == 'op' and 'history' in entry and entry['history']:
                subject_str = entry['history'][0].get('subject')

            if subject_str and role == 'op': 
                rendered_elements.append(Padding(Markdown(f"**{subject_str.strip()}**"), (0,0,0, (indent * 4) + 2)))

            # 3. Main Content (HTML to Markdown)
            html_content_str = None
            if role == 'op' and 'history' in entry and entry['history']:
                html_content_str = entry['history'][0].get('content')
            elif role in ['student', 'instructor']: 
                html_content_str = entry.get('content') 
                answer_subject = entry.get('subject')
                if answer_subject: 
                    html_content_str = f"<h3>{answer_subject}</h3>{html_content_str if html_content_str else ''}"
            elif role == 'followup': 
                html_content_str = entry.get('subject') 
            elif role == 'comment': 
                html_content_str = entry.get('subject') 
            elif role == 'note': # Added content extraction for 'note'
                html_content_str = entry.get('subject') # Notes usually have content in subject
            
            if html_content_str:
                processed_html_content = ""
                try:
                    raw_converted = html_to_markdown(str(html_content_str)) 
                    if raw_converted is not None:
                        processed_html_content = str(raw_converted).strip()
                    else:
                        processed_html_content = "[Content conversion error: None returned]"
                except Exception as e:
                    processed_html_content = f"[Error converting content: {type(e).__name__} - {e}]"
                
                if processed_html_content: 
                    rendered_elements.append(Padding(Markdown(processed_html_content), (0, 0, 0, (indent * 4) + 2)))
            elif role == 'op' and not subject_str and ('history' not in entry or not entry['history'] or not entry['history'][0].get('content')):
                 rendered_elements.append(Padding(Text("[Empty post content]", style="dim"), (0,0,0, (indent * 4) +2)))

            # 4. Endorsements
            if 'tag_good_arr' in entry and entry['tag_good_arr']:
                endorsers_display = []
                for tag_info_item in entry['tag_good_arr']:
                    if isinstance(tag_info_item, dict):
                        endorser_name = tag_info_item.get('endorser_name', 'An endorser')
                        endorser_role = f" ({tag_info_item.get('role', 'user')})" if tag_info_item.get('role') else ""
                        endorsers_display.append(f"{endorser_name}{endorser_role}")
                    elif isinstance(tag_info_item, str): # Handle case where tag_info_item is a string (e.g., user ID)
                        endorsers_display.append(tag_info_item) # Display the string directly
                    # else: could add handling for other unexpected types
                if endorsers_display:
                    endorsement_line = Text(f"~ Endorsed by: {', '.join(endorsers_display)} ~", style="italic yellow")
                    rendered_elements.append(Padding(endorsement_line, (0,0,0, (indent * 4) + 4))) 
            
            # 5. Poll Data (Basic Rendering)
            if role == 'op' and entry.get('type') == 'poll':
                poll_info_parts = [Text("[POLL]", style="bold cyan")]
                poll_data = entry.get('poll') # Standard field for poll info in Piazza API
                if poll_data and 'options' in poll_data and isinstance(poll_data['options'], list):
                    options_map = {opt['id']: opt.get('text', 'Option') for opt in poll_data['options']}
                    # Results are often in poll_data['results'] (map of option_id to votes)
                    # or sometimes directly in options if votes are embedded.
                    # Let's assume poll_data['results'] exists.
                    results = poll_data.get('results', {}) 
                    
                    for opt_id, opt_text in options_map.items():
                        votes = results.get(opt_id, 0)
                        poll_info_parts.append(Text(f"\\n- {opt_text} ({votes} votes)", style="cyan"))
                else:
                    poll_info_parts.append(Text(" [Details not available or structure unexpected]", style="dim cyan"))
                
                rendered_elements.append(Padding(Text.assemble(*poll_info_parts), (0,0,0, (indent * 4)+2)))

            if not rendered_elements and str(prefix): # Only prefix was rendered, add placeholder
                 rendered_elements.append(Padding(Text("...", style="dim"), (0,0,0, (indent*4)+2)))
            elif not rendered_elements and not str(prefix): # Absolutely nothing, render a minimal marker
                 rendered_elements.append(Padding(Text("[empty entry]", style="dim"), (0,0,0, (indent*4)+2)))


            return rendered_elements

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

            # 2. Process all direct children: student answers, instructor answers, followups, and notes
            children_of_post = current_post_data.get('children', [])
            if children_of_post:
                for child_item in children_of_post:
                    item_type = child_item.get('type')
                    role_to_render = None
                    
                    if item_type == 's_answer':
                        role_to_render = 'student'
                    elif item_type == 'i_answer':
                        role_to_render = 'instructor'
                    elif item_type == 'followup':
                        role_to_render = 'followup'
                    elif item_type == 'note':
                        role_to_render = 'note'
                    else:
                        # Fallback for unknown types: try rendering as a 'comment'
                        # This helps catch other potential content if types are unexpected.
                        # A debug print here would be useful: console.print(f"[DEBUG] Unknown child type: {item_type} in post {current_post_data.get('nr', 'N/A')}. Rendering as comment.")
                        role_to_render = 'comment' 

                    if role_to_render:
                        output_lines.extend(render_entry(child_item, indent=1, role=role_to_render))
                        # Recursively walk children of this item (comments on answers, followups, notes, etc.)
                        if child_item.get('children'): 
                            output_lines.extend(walk_children(child_item['children'], 2))
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

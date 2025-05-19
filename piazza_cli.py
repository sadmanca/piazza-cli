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

class PiazzaCLI(Cmd):
    intro = "Welcome to Piazza CLI. Type help or ? to list commands."
    prompt = "piazza> "

    def __init__(self):
        super().__init__()
        self.piazza = Piazza()
        self.logged_in = False
        self.courses = []
        self.current_course = None
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
        """Interactively select a course and view questions (arrow keys, Enter to select). Press [q] to exit, [h] for help, [l] to logout, [s] to search."""
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
            # Compose subject and content, convert HTML to Markdown
            body = ""
            if entry.get('subject'):
                body += f"**{entry['subject']}**\n\n" # Add extra newline for separation
            content = entry.get('content', '')
            if content:
                body += html_to_markdown(content)
            
            from rich.padding import Padding
            rendered_elements = []
            
            # Render prefix as a single line (Text)
            if str(prefix):
                rendered_elements.append(Padding(prefix, (0, 0, 0, indent * 4)))
            
            # Render Markdown body, allowing Rich to handle wrapping
            # The Markdown object itself will be padded.
            # The width of the console is implicitly handled by the Panel/Live display.
            if body.strip(): # Only add Markdown if there's content
                md = Markdown(body)
                rendered_elements.append(Padding(md, (0, 0, 0, indent * 4)))
            
            return rendered_elements

        def walk_children(children, indent):
            lines = []
            for child in children:
                # Determine role
                role = None
                if child.get('type') == 'i_answer':
                    role = 'instructor'
                elif child.get('type') == 's_answer':
                    role = 'student'
                elif child.get('type') == 'feedback':
                    role = 'comment'
                # For answers, use their history[0] as entry
                if 'history' in child and child['history']:
                    lines.extend(render_entry(child['history'][0], indent, role))
                else:
                    lines.extend(render_entry(child, indent, role))
                # Recursively walk children/comments
                if 'children' in child and child['children']:
                    lines.extend(walk_children(child['children'], indent + 1))
            return lines

        def walk_thread(post):
            lines = []
            # Main post
            main = post['history'][0]
            lines.extend(render_entry(main, 0, 'op'))
            # Answers (instructor/student)
            if 'children' in post:
                lines.extend(walk_children(post['children'], 1))
            # Followups and their comments
            if 'followups' in post:
                for f in post['followups']:
                    lines.extend(render_entry(f, 1, 'followup'))
                    if 'children' in f and f['children']:
                        lines.extend(walk_children(f['children'], 2))
            return lines

        thread_lines = walk_thread(post)
        # Flatten all lines for scrolling, regardless of entry
        window_size = 12
        pos = 0
        total = len(thread_lines)

        def render_window():
            visible = thread_lines[pos:pos+window_size]
            group = Group(*visible)
            panel = Panel(group, title=f"Post {post_nr} (Up/Down to scroll, c=comment, b=back)", border_style="cyan", expand=False)
            return Align.center(panel)

        with Live(render_window(), refresh_per_second=10, console=console, screen=True) as live:
            while True:
                event = keyboard.read_event(suppress=True)
                if event.event_type == keyboard.KEY_DOWN:
                    if event.name == 'up':
                        if pos > 0:
                            pos -= 1
                            live.update(render_window())
                    elif event.name == 'down':
                        if pos < total - window_size:
                            pos += 1
                            live.update(render_window())
                    elif event.name == 'c':
                        console.print("[bold green]Enter your comment below. Press Enter to submit.[/bold green]")
                        content = input("Comment: ").strip()
                        if content:
                            try:
                                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                                    progress.add_task(description="Posting comment...", total=None)
                                    net.create_followup(post_nr, content)
                                console.print("[green]Comment posted.[/green]")
                            except Exception as e:
                                console.print(f"[red]Failed to post comment:[/red] {e}")
                            break
                    elif event.name in ('b', 'esc', 'q'):
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

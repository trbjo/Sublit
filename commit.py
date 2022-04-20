import codecs
import functools
import tempfile
import os
from subprocess import check_output, Popen

import sublime
import sublime_plugin
from . import GitTextCommand, GitWindowCommand, plugin_file, view_contents, _make_text_safeish
from .add import GitAddSelectedHunkCommand

history = []


class PromptGitCommand(GitWindowCommand):
    last_selected = 0

    def is_enabled(self):
        view = self.window.active_view()
        if view:
            scope = view.scope_name(view.sel()[0].begin()).split(' ')[0]
            if scope == 'text.git.commit':
                return True
        return False

    def run(self):
        git_actions_pretty = [
        '1: Commit, Rebase and Push',
        '2: Commit and Push',
        '3: Commit only',
        '4: Close without committing'
        ]

        self.window.show_quick_panel(
            git_actions_pretty,
            self.transform,
            selected_index=self.last_selected)

    def transform(self, i: int) -> None:
        view = sublime.active_window().active_view()
        if view is None:
            return
        file_name = view.file_name()
        if file_name is None:
            return

        pwd = file_name.rsplit('/', 1)[0]

        if i == -1:
            return
        self.last_selected: int = i

        if i != 3:
            view.run_command('save')

        view.set_scratch(True)
        view.close()

        if i >= 2:
            return

        if i == 0:
            self.run_command(['git', '-C', pwd, 'pull', '--rebase'], callback=self.push, working_dir=pwd)
        else:
            self.push(pwd)

    def push(self, _) -> None:
        self.run_command(['git', 'push'])


class GitCommitCommand(sublime_plugin.WindowCommand):
    global git_dirs
    global non_git_dirs
    git_dirs = []
    non_git_dirs = []

    def anything_to_commit(self, pwd: str) -> bool:
        get_status = ['git', '-C', pwd, 'status', '--porcelain', '--untracked-files=no']
        res_list = check_output(get_status).decode('utf-8').split('\n')
        return any(git_status[0].isalpha() for git_status in res_list if git_status != '')

    def run(self):
        pwd = self.window.active_view().file_name().rsplit('/', 1)[0]
        try:
            Popen(['git', '-C', pwd, 'commit', '-v'])
        except:
            sublime.status_message('Nothing to commit')

    def is_enabled(self):
        view = self.window.active_view()
        if view is None:
            return False

        file = view.file_name()
        if file is None:
            return False

        pwd = file.rsplit('/', 1)[0]
        if pwd.endswith('.git'):
            pwd = pwd[0:-4]

        for repo in git_dirs:
            if repo in pwd:
                return self.anything_to_commit(pwd)
        for directory in non_git_dirs:
            if directory == pwd:
                return False

        result = self.is_git_dir(pwd)
        if result:
            git_dirs.append(result)
            return self.anything_to_commit(pwd)
        else:
            non_git_dirs.append(pwd)
            return False


    def is_git_dir(self, pwd: str) -> str:
        while pwd:
            if os.path.exists(os.path.join(pwd, '.git')):
                return pwd
            parent = os.path.realpath(os.path.join(pwd, os.path.pardir))
            if parent == pwd:
                # /.. == /
                return ''
            pwd = parent



class GitQuickCommitCommand(GitTextCommand):
    def run(self, edit, target=None):
        if target is None:
            # 'target' might also be False, in which case we just don't provide an add argument
            target = self.get_file_name()
        self.get_window().show_input_panel(
            "Message", "",
            functools.partial(self.on_input, target), None, None
        )

    def on_input(self, target, message):
        if message.strip() == "":
            self.panel("No commit message provided")
            return

        if target:
            command = ['git', 'add']
            if target == '*':
                command.append('--all')
            else:
                command.extend(('--', target))
            self.run_command(command, functools.partial(self.add_done, message))
        else:
            self.add_done(message, "")

    def add_done(self, message, result):
        if result.strip():
            sublime.error_message("Error adding file:\n" + result)
            return
        self.run_command(['git', 'commit', '-m', message])


class GitCommitAmendCommand(GitCommitCommand):
    extra_options = "--amend"
    quit_when_nothing_staged = False

    def diff_done(self, result):
        self.after_show = result
        self.run_command(['git', 'log', '-n', '1', '--format=format:%B'], self.amend_diff_done)

    def amend_diff_done(self, result):
        self.lines = result.split("\n")
        super(GitCommitAmendCommand, self).diff_done(self.after_show)


class GitCommitMessageListener(sublime_plugin.EventListener):
    def on_close(self, view):
        if view.name() != "COMMIT_EDITMSG":
            return
        command = GitCommitCommand.active_message
        if not command:
            return
        message = view_contents(view)
        command.message_done(message)


class GitCommitHistoryCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.edit = edit
        if history:
            self.view.window().show_quick_panel(history, self.panel_done, sublime.MONOSPACE_FONT)
        else:
            sublime.message_dialog("You have no commit history.\n\nCommit history is just a quick list of messages you've used in this session.")

    def panel_done(self, index):
        if index > -1:
            self.view.replace(self.edit, self.view.sel()[0], history[index] + '\n')


class GitCommitSelectedHunk(GitAddSelectedHunkCommand):
    def cull_diff(self, result):
        super(GitCommitSelectedHunk, self).cull_diff(result)
        self.get_window().run_command('git_commit')

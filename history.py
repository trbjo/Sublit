import functools
import re

import sublime

from . import GitTextCommand, GitWindowCommand, plugin_file


class GitLog(object):
    def run(self, edit=None):
        fn = self.get_file_name()
        return self.run_log(fn != "", "--", fn)

    def run_log(self, follow, *args):
        # the ASCII bell (\a) is just a convenient character I'm pretty sure
        # won't ever come up in the subject of the commit (and if it does then
        # you positively deserve broken output...)
        # 9000 is a pretty arbitrarily chosen limit; picked entirely because
        # it's about the size of the largest repo I've tested this on... and
        # there's a definite hiccup when it's loading that
        command = [
            "git",
            "log",
            "--no-color",
            "--pretty=%s (%h)\a%an <%aE>\a%ad (%ar)",
            "--date=local",
            "--max-count=9000",
            "--follow" if follow else None,
        ]
        command.extend(args)
        self.run_command(command, self.log_done)

    def log_done(self, result):
        self.results = [r.split("\a", 2) for r in result.strip().split("\n")]
        self.quick_panel(self.results, self.log_panel_done)

    def log_panel_done(self, picked):
        if 0 > picked < len(self.results):
            return
        item = self.results[picked]
        # the commit hash is the last thing on the first line, in brackets
        ref = item[0].split(" ")[-1].strip("()")
        self.log_result(ref)

    def log_result(self, ref):
        # I'm not certain I should have the file name here; it restricts the
        # details to just the current file. Depends on what the user expects...
        # which I'm not sure of.
        self.run_command(
            ["git", "log", "--no-color", "-p", "-1", ref, "--", self.get_file_name()],
            self.details_done,
        )

    def details_done(self, result):
        self.scratch(
            result,
            title="Git Commit Details",
            syntax="Packages/Git Formats/Git Log.sublime-syntax",
        )


class GitLogCommand(GitLog, GitTextCommand):
    pass


class GitLogAllCommand(GitLog, GitWindowCommand):
    pass


class GitShow(object):
    def run(self, edit=None):
        # GitLog Copy-Past
        self.run_command(
            [
                "git",
                "log",
                "--no-color",
                "--pretty=%s (%h)\a%an <%aE>\a%ad (%ar)",
                "--date=local",
                "--max-count=9000",
                "--",
                self.get_file_name(),
            ],
            self.show_done,
        )

    def show_done(self, result):
        # GitLog Copy-Past
        self.results = [r.split("\a", 2) for r in result.strip().split("\n")]
        self.quick_panel(self.results, self.panel_done)

    def panel_done(self, picked):
        if 0 > picked < len(self.results):
            return
        item = self.results[picked]
        # the commit hash is the last thing on the first line, in brackets
        ref = item[0].split(" ")[-1].strip("()")
        self.run_command(
            ["git", "show", "%s:%s" % (ref, self.get_relative_file_path())],
            self.details_done,
            ref=ref,
        )

    def details_done(self, result, ref):
        syntax = self.view.settings().get("syntax")
        self.scratch(result, title="%s:%s" % (ref, self.get_file_name()), syntax=syntax)


class GitShowCommand(GitShow, GitTextCommand):
    pass


class GitShowAllCommand(GitShow, GitWindowCommand):
    pass


class GitShowCommitCommand(GitWindowCommand):
    def run(self, edit=None):
        self.window.show_input_panel("Commit to show:", "", self.input_done, None, None)

    def input_done(self, commit):
        commit = commit.strip()

        self.run_command(["git", "show", commit, "--"], self.show_done, commit=commit)

    def show_done(self, result, commit):
        if result.startswith("fatal:"):
            self.panel(result)
            return
        self.scratch(
            result,
            title="Git Commit: %s" % commit,
            syntax="Packages/Git Formats/Git Log.sublime-syntax",
        )


class GitGraph(object):
    def run(self, edit=None):
        filename = self.get_file_name()
        self.run_command(
            [
                "git",
                "log",
                "--graph",
                "--pretty=%h -%d (%cr) (%ci) <%an> %s",
                "--abbrev-commit",
                "--no-color",
                "--decorate",
                "--date=relative",
                "--follow" if filename else None,
                "--",
                filename,
            ],
            self.log_done,
        )

    def log_done(self, result):
        self.scratch(
            result,
            title="Git Log Graph",
            syntax=plugin_file("syntax/Git Graph.tmLanguage"),
        )


class GitOpenFileCommand(GitLog, GitWindowCommand):
    def run(self):
        self.run_command(["git", "branch", "-a", "--no-color"], self.branch_done)

    def branch_done(self, result):
        self.results = result.rstrip().split("\n")
        self.quick_panel(self.results, self.branch_panel_done, sublime.MONOSPACE_FONT)

    def branch_panel_done(self, picked):
        if 0 > picked < len(self.results):
            return
        self.branch = self.results[picked].split(" ")[-1]
        self.run_log(False, self.branch)

    def log_result(self, result_hash):
        self.ref = result_hash
        self.run_command(
            ["git", "ls-tree", "-r", "--full-tree", self.ref], self.ls_done
        )

    def ls_done(self, result):
        # Last two items are the ref and the file name
        # p.s. has to be a list of lists; tuples cause errors later
        self.results = [
            [match.group(2), match.group(1)]
            for match in re.finditer(r"\S+\s(\S+)\t(.+)", result)
        ]

        self.quick_panel(self.results, self.ls_panel_done)

    def ls_panel_done(self, picked):
        if 0 > picked < len(self.results):
            return
        item = self.results[picked]

        self.filename = item[0]
        self.fileRef = item[1]

        self.run_command(["git", "show", self.fileRef], self.show_done)

    def show_done(self, result):
        self.scratch(result, title="%s:%s" % (self.fileRef, self.filename))


class GitGotoCommit(GitTextCommand):
    def run(self, edit):
        view = self.view

        # Sublime is missing a "find scope in region" API, so we piece one together here:
        lines = [view.line(sel.a) for sel in view.sel()]
        hashes = self.view.find_by_selector("string.sha")
        commits = []
        for region in hashes:
            for line in lines:
                if line.contains(region):
                    commit = view.substr(region)
                    if commit.strip("0"):
                        commits.append(commit)
                    break

        working_dir = view.settings().get("git_root_dir")
        for commit in commits:
            self.run_command(
                ["git", "show", commit], self.show_done, working_dir=working_dir
            )

    def show_done(self, result):
        self.scratch(
            result,
            title="Git Commit View",
            syntax="Packages/Git Formats/Git Log.sublime-syntax",
        )

    def is_enabled(self):
        if self.view.element() is not None:
            return False
        selection = self.view.sel()[0]
        return self.view.match_selector(
            selection.a, "text.git-blame"
        ) or self.view.match_selector(selection.a, "text.git-graph")

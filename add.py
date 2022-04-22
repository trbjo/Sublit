import os
import re

import sublime
from . import GitTextCommand, GitWindowCommand, git_root
from .status import GitStatusCommand

class GitAddUntrackedFileCommand(GitTextCommand):
    def run(self, _):
        self.run_command(['git', 'add', self.get_file_name()])

class GitAddChoiceCommand(GitStatusCommand):
    def status_filter(self, item):
        return super(GitAddChoiceCommand, self).status_filter(item) and not item[1].isspace()

    def show_status_list(self):
        self.results = [
            [" + All Files", "apart from untracked files"],
            [" + All Files", "including untracked files"],
        ] + [[a, ''] for a in self.results]
        return super(GitAddChoiceCommand, self).show_status_list()

    def panel_followup(self, picked_status, picked_file, picked_index):
        working_dir = git_root(self.get_working_dir())

        if picked_index == 0:
            command = ['git', 'add', '--update']
        elif picked_index == 1:
            command = ['git', 'add', '--all']
        else:
            command = ['git']
            picked_file = picked_file.strip('"')
            if os.path.exists(working_dir + "/" + picked_file):
                command += ['add']
            else:
                command += ['rm']
            command += ['--', picked_file]

        self.run_command(
            command, self.rerun,
            working_dir=working_dir
        )

    def rerun(self, result):
        self.run()


class GitAddSelectedHunkCommand(GitTextCommand):
    def calculate_header(self, start_before: int, start_after: int,
                         plus_lines: int, minus_lines: int,
                         context_before: bool, context_after: bool,
                         buf_length: int, context_lines: int,
                         first_line_is_delete: bool) -> str:

        context = context_lines
        if context_before == True:
            context += 1
        if context_after == True:
            context += 1

        if start_before == 0 or start_before ==1:
            first = 1
            third = 1
        elif buf_length == start_after + plus_lines - 1 or buf_length == start_after:
            first = start_before if minus_lines == 0 else start_before - 1
            third = start_after if plus_lines == 0 else start_after - 1
        else:
            first = start_before if first_line_is_delete is False else start_before - 1
            third = start_after if plus_lines == 0 else start_after - 1

        second = minus_lines + context
        fourth = plus_lines + context

        if second == 0:
            header = f'@@ -{first} +{third},{fourth} @@'
        elif fourth == 0:
            header = f'@@ -{first},{second} +{third} @@'
        else:
            header = f'@@ -{first},{second} +{third},{fourth} @@'

        return header


    def run(self, _):
        self.run_command(['git', 'diff', '--no-color', '-U0', self.get_file_name()], self.cull_diff)

    def cull_diff(self, result):
        buf = self.view

        # lines are 1-based but we have trailing newline set
        # which cancels each other out
        buf_length, _ = buf.rowcol(buf.size())

        hunks = [{"diff": ""}]
        i = 0
        matcher = re.compile(r'^@@ -([0-9]*)(?:,([0-9]*))? \+([0-9]*)(?:,([0-9]*))? @@')
        plus_lines = 0
        minus_lines = 0
        context_lines = 0
        context_before = False
        context_after = False
        for line in result.splitlines():
            match = None
            if line.startswith('@@'):

                # new hunk, so we record the length of the old and reset the counters
                # but not the first iter since that contains the diff header
                if i > 0:
                    hunks[i]['plus_lines'] = plus_lines
                    hunks[i]['minus_lines'] = minus_lines
                    hunks[i]['context_lines'] = context_lines
                    plus_lines = 0
                    minus_lines = 0
                    context_before = False
                    context_after = False

                i += 1
                match = matcher.match(line)

                # first and third always exist
                start_before = int(match.group(1))
                first_line_is_delete = False if match.group(2) == '0' else True
                start_after = int(match.group(3))
                end = match.group(4)

                # do not add context lines if we are in the beginning of the buffer
                # or clause deals with deletes on second line
                if start_after > 1 or (start_after == 1 and end and int(end) == 0):
                    context_before = True

                if end:
                    if int(end) != 0:
                        end = int(end) + start_after - 1
                    else:
                        end = int(end) + start_after

                else:
                    end = start_after

                # do not add context lines, if the diff affects the buffer end
                # we exclude the final newline
                # minus one means we exclude the final newline, and
                # the plus one is because sublime's line index is 0-based

                lines_of_buf = buf.rowcol((buf.size() -1))[0] + 1

                if end < lines_of_buf:
                    context_after = True

                hunks.append({"diff": "", "minus_lines": minus_lines, "context_before": context_before, "context_after": context_after, "context_lines": 0, "start_before": start_before, "start_after": start_after, "plus_lines": plus_lines, "end": end, "first_line_is_delete": first_line_is_delete})
            else:
                if i > 0:
                    if line.startswith('+'):
                        plus_lines += 1
                    elif line.startswith('-'):
                        minus_lines += 1
                hunks[i]["diff"] += line + "\n"

        # when we are done with the loop, we store it in the dict.
        hunks[i]['plus_lines'] = plus_lines
        hunks[i]['minus_lines'] = minus_lines
        hunks[i]['context_before'] = context_before
        hunks[i]['context_after'] = context_after

        diffs = hunks[0]["diff"]
        hunks.pop(0)
        difflist = []
        prev_next_line_num = -99
        j = -1
        for hunk in hunks:
            hunk_start = int(hunk["start_after"])
            hunk_end = int(hunk["end"])
            for region in buf.sel():

                sel_start = buf.rowcol(region.begin())[0] + 1
                sel_end = buf.rowcol(region.end())[0] + 1

                # Sublime's behavior is such that when a diff has only deletions, the next_modification
                # command will move the caret to the line AFTER the change, thus our prevline becomes off-by-one.
                # Only deletions means plus_lines == 0. Therefore, the red diff marker in the sublime
                # gutter signifies plus_lines == 0.

                if hunk['plus_lines'] == 0:
                    if sel_end - 1 < hunk_start:
                        continue
                    elif sel_start - 1 > hunk_end:
                        if not (sel_start -2 == hunk_start and sel_end -2 == hunk_end):
                            continue

                    prev_line_offset = 1

                else:
                    if sel_end < hunk_start:
                        continue
                    elif sel_start > hunk_end:
                        continue

                    prev_line_offset = 2

                prev_line_num_one_indexed = hunk_start - prev_line_offset + 1
                # composing diffs
                if prev_line_num_one_indexed == prev_next_line_num:

                    # Icrease context lines by at least the binding line between
                    difflist[j]['context_lines'] += 1

                    difflist[j]['minus_lines'] += hunk['minus_lines']
                    difflist[j]['plus_lines'] += hunk['plus_lines']

                    if hunk['context_after'] == True:
                        next_line = buf.substr(buf.full_line(buf.text_point(hunk_end,0)))
                        difflist[j]['diff'] += hunk["diff"] + ' ' + next_line
                    else:
                        difflist[j]['diff'] += hunk["diff"]

                    # Last hunk decides if the context after should be included
                    difflist[j]['context_after'] = hunk['context_after']

                    prev_next_line_num = hunk_end + 1
                    continue

                else:

                    if hunk['context_before'] == True:
                        prev_line_num = hunk_start - prev_line_offset
                        prev_line = buf.substr(buf.full_line(buf.text_point(prev_line_num,0)))
                        hunk["diff"] = ' ' + prev_line + hunk["diff"]

                    if hunk['context_after'] == True:
                        next_line = buf.substr(buf.full_line(buf.text_point(hunk_end,0)))
                        hunk["diff"] += ' ' + next_line

                    prev_next_line_num = hunk_end + 1
                    difflist.append(hunk)
                    j += 1
                    break

        if len(difflist) > 0:
            for diff in difflist:
                header = self.calculate_header(diff['start_before'], diff['start_after'],
                                          diff['plus_lines'], diff['minus_lines'],
                                          diff['context_before'], diff['context_after'],
                                          buf_length, diff['context_lines'],
                                          diff['first_line_is_delete'])
                diffs += header + '\n' + diff["diff"]
            self.run_command(['git', 'apply', '--cached', '--ignore-space-change', '--ignore-whitespace'], stdin=diffs)
        else:
            sublime.status_message("No selected hunk")


# Also, sometimes we want to undo adds


class GitResetHead(object):
    def run(self, edit=None):
        self.run_command(['git', 'reset', 'HEAD', self.get_file_name()])

    def generic_done(self, result):
        pass


class GitResetHeadCommand(GitResetHead, GitTextCommand):
    pass


class GitResetHeadAllCommand(GitResetHead, GitWindowCommand):
    pass


class GitResetHardHeadCommand(GitWindowCommand):
    may_change_files = True

    def run(self):
        if sublime.ok_cancel_dialog("Warning: this will reset your index and revert all files, throwing away all your uncommitted changes with no way to recover. Consider stashing your changes instead if you'd like to set them aside safely.", "Continue"):
            self.run_command(['git', 'reset', '--hard', 'HEAD'])

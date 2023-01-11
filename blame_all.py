from enum import IntEnum
from typing import List, Tuple, Union

import sublime
import sublime_plugin
from sublime import LAYOUT_INLINE, Edit, Region, View
from sublime_api import view_add_phantom, view_erase_phantoms

from .base import BaseBlame


class Dim(IntEnum):
    UNSET = 0
    YES = 1
    NO = 2


class HunkType(IntEnum):
    NOT_COMMITTED = -1
    SAME_AS_PREV_LINE = -2
    NEW_HUNK = -3


VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED = "git-blame-all-displayed"

VIEW_SETTINGS_KEY_RULERS = "rulers"  # A stock ST setting
VIEW_SETTINGS_KEY_RULERS_PREV = "rulers_prev"  # Made up by us

VIEW_SETTINGS_KEY_WRAP = "word_wrap"  # Made up by us
VIEW_SETTINGS_KEY_WRAP_PREV = "word_wrap_prev"  # Made up by us

VIEW_SETTINGS_KEY_INDENT_GUIDE = "draw_indent_guides"  # Made up by us
VIEW_SETTINGS_KEY_INDENT_GUIDE_PREV = "draw_indent_guides_prev"  # Made up by us

color_list = [
    "redish",
    "orangish",
    "purplish",
    "yellowish",
    "greenish",
    "cyanish",
    "bluish",
    "pinkish",
]


class BlameWatcher(BaseBlame, sublime_plugin.ViewEventListener):
    def _view(self) -> View:
        return self.view

    def extra_cli_args(self, **kwargs):
        return []

    def close_by_user_request(self):
        self.view.run_command("blame_erase_all")

    def rerun(self, **kwargs):
        self.run(None)

    def on_modified(self):
        if not self.view.settings().get("shas"):
            return

        self.view.settings().erase("shas")
        view_erase_phantoms(self.view.id(), "blame_all")
        self.view.settings().set(
            VIEW_SETTINGS_KEY_RULERS,
            self.view.settings().get(VIEW_SETTINGS_KEY_RULERS_PREV),
        )
        self.view.settings().set(
            VIEW_SETTINGS_KEY_INDENT_GUIDE,
            self.view.settings().get(VIEW_SETTINGS_KEY_INDENT_GUIDE_PREV),
        )
        self.view.settings().set(
            VIEW_SETTINGS_KEY_WRAP,
            self.view.settings().get(VIEW_SETTINGS_KEY_WRAP_PREV),
        )

    def on_hover(self, point: int, hover_zone: int) -> None:
        if not self.view.settings().get(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED):
            return
        if hover_zone == sublime.HOVER_MARGIN:
            return
        file_name: Union[str, None] = self.view.file_name()
        if file_name is None:
            print("Buffer does not have a file, aborting")
            return

        point_to_line, col = self.view.rowcol(point)
        if col != 0:
            return

        shas = self.view.settings().get("shas", [])
        if not shas:
            return
        sha: str = shas[point_to_line]
        if sha == len(sha) * "0":
            self.view.show_popup(
                '<body style="padding: 4px; margin: 0; font-family: system-ui;"><div>Not committed yet</div></body>',
                location=point,
                flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            )
            return

        try:
            raw_desc = self.get_commit_desc(sha, file_name)
            elems: List[str] = raw_desc.rstrip().split("\n", 1)
            commmit_id = elems[0][7:]
            desc: str = elems[1].replace("\n", "<br>")
            popup_text = f'<body style="padding: 4px; margin: 0; font-family: system-ui;"><a href="copy?sha={commmit_id}">{commmit_id}</a><div>{desc}</div></body>'
        except Exception as e:
            self.communicate_error(e)
            return

        self.view.show_popup(
            popup_text,
            location=point,
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            max_width=500,
            on_navigate=self.handle_phantom_button,
        )


class BlameShowAll(BaseBlame, sublime_plugin.TextCommand):
    HORIZONTAL_SCROLL_DELAY_MS = 100

    # Overrides (TextCommand) ----------------------------------------------------------
    def __init__(self, view: View):
        super().__init__(view)
        self.key_name: str = "blame_all"
        self.regs_ready_formatting: List[Tuple[Region, str]] = []
        self.raw_list_formatting: List[
            Union[Tuple[HunkType, int], Tuple[HunkType, int, str, str, str, str, bool]]
        ] = []
        self.pattern = None
        self.empty_html: str = ""
        self.max_author_len: int = 13
        self.actual_author_max_len: int = 0
        self.sha_length: int = 0
        self.highlighted_commit = ""

    def highlight_this_commit(self, href: str) -> None:
        if not self.raw_list_formatting:
            self.view.hide_popup()
            view_erase_phantoms(self.view.id(), self.key_name)
            self.view.settings().erase(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED)
            self.view.run_command("blame_restore_rulers")
            # Workaround a visible empty space sometimes remaining in the viewport.
            self.horizontal_scroll_to_limit(left=False)
            self.horizontal_scroll_to_limit(left=True)
            return

        if href == self.highlighted_commit:
            self.phantom_setter()
            self.highlighted_commit = ""
        else:
            self.phantom_setter(href)
            self.highlighted_commit = href

    def format_author(self, author: str) -> str:
        if len(author) > self.actual_author_max_len:
            ret_str: str = author[: self.actual_author_max_len - 1] + "…"
        else:
            ret_str: str = author
        return ret_str + "&nbsp;" * (self.actual_author_max_len - len(author))

    def region_creator(
        self,
        line_number: int,
        sha_color: str,
        sha: str,
        author: str,
        date: str,
        sha_dim: str,
        text_dim: str,
    ) -> Tuple[Region, str]:
        return (
            Region(self.view.text_point(line_number - 1, 0)),
            f"""<body style="padding: 0px 6px 4px 0; margin: 0; border-right: 5px solid color(var(--{sha_color}) blend(var(--background) {sha_dim}%)));"><a style="text-decoration:none;" href="{sha}"><span style="color: color(var(--foreground) blend(var(--background){text_dim}%));" class="message">{sha}&nbsp;&nbsp;{author}&nbsp;&nbsp;{date}</span></a></body>""",
        )

    def phantom_setter(self, hl_sha: Union[str, None] = None) -> None:
        if self.actual_author_max_len > self.max_author_len:
            self.actual_author_max_len = self.max_author_len
        space_string: str = (
            self.actual_author_max_len + 14 + self.sha_length
        ) * "&nbsp;"

        self.regs_ready_formatting = []
        sha_color: str = ""
        sha: str = ""
        author: str = ""
        date: str = ""
        color_dim: str = ""
        for line in self.raw_list_formatting:
            line_number: int = line[1]

            if line[0] == HunkType.SAME_AS_PREV_LINE:
                self.regs_ready_formatting.append(
                    (
                        Region(self.view.text_point(line_number - 1, 0)),
                        f'<body style="padding: 0px 6px 4px 0; margin: 0; border-right: 5px solid color(var(--{sha_color}) blend(var(--background) {color_dim}%)));"> <a style="text-decoration:none;" href="{sha}"><span class="message">{space_string}</span></a></body>',
                    )
                )
            elif line[0] == HunkType.NOT_COMMITTED:
                sha_color: str = "foreground"
                sha = "0" * self.sha_length
                author: str = self.format_author("Not committed yet")
                date: str = "0000-00-00"
                if hl_sha is not None:
                    if sha == hl_sha:
                        color_dim = "70"
                        text_dim = "70"
                    else:
                        color_dim = "10"
                        text_dim = "10"
                else:
                    color_dim: str = "40"
                    text_dim = "25"
                self.regs_ready_formatting.append(
                    self.region_creator(
                        line_number, sha_color, sha, author, date, color_dim, text_dim
                    )
                )
            elif line[0] == HunkType.NEW_HUNK:
                sha_color: str = line[2]
                sha: str = line[3]
                author: str = self.format_author(line[4])
                date: str = line[5]
                if hl_sha is not None:
                    if sha == hl_sha:
                        color_dim = "100"
                        text_dim = "70"
                    else:
                        color_dim = "10"
                        text_dim = "10"
                else:
                    color_dim: str = "40" if line[6] else "100"
                    text_dim = "25"
                self.regs_ready_formatting.append(
                    self.region_creator(
                        line_number, sha_color, sha, author, date, color_dim, text_dim
                    )
                )
            else:
                raise Exception("Invalid HunkType")

        self.set_phantoms_from_regions()

    def set_phantoms_from_regions(self) -> None:
        buffer_id = self.view.id()
        self.view.erase_phantoms(self.key_name)
        for line in self.regs_ready_formatting:
            view_add_phantom(
                buffer_id,
                self.key_name,
                line[0],
                line[1],
                LAYOUT_INLINE,
                self.highlight_this_commit,
            )

    def run(self, edit: Edit):
        if not self.has_suitable_view():
            self.tell_user_to_save()
            return

        file_name = self.view.file_name()
        if file_name is None:
            return

        view_erase_phantoms(self.view.id(), self.key_name)

        # If they are currently shown, toggle them off and return.
        if self.view.settings().get(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED, False):
            self.view.hide_popup()
            self.view.settings().erase(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED)
            self.view.run_command("blame_restore_rulers")
            # Workaround a visible empty space sometimes remaining in the viewport.
            self.horizontal_scroll_to_limit(left=False)
            self.horizontal_scroll_to_limit(left=True)
            return

        self.view.settings().set(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED, True)
        self.settings_for_blame()
        # Bring the phantoms into view without the user needing to manually scroll left.
        self.horizontal_scroll_to_limit(left=True)

        if self.regs_ready_formatting and self.view.settings().get("shas", False):
            self.set_phantoms_from_regions()
            return

        try:
            blame_output = self.get_blame_text(file_name)
        except Exception as e:
            self.communicate_error(e)
            return

        blames = [self.parse_line(line) for line in blame_output.splitlines()]
        blames = [b for b in blames if b]
        if not blames:
            self.communicate_error(
                "Failed to parse anything for {0}. Has git's output format changed?".format(
                    self.__class__.__name__
                )
            )
            return

        hash_color = {}
        self.sha_length = len(blames[0]["sha"])
        self.raw_list_formatting = []
        counter = 0
        prev_sha = ""
        dim = False
        shas: List[str] = []
        for blame in blames:
            sha: str = blame["sha"]
            shas.append(sha)
            line_number = int(blame["line_number"])

            if prev_sha == sha:
                phantom = (HunkType.SAME_AS_PREV_LINE, line_number)
            elif sha == self.sha_length * "0":
                phantom = (HunkType.NOT_COMMITTED, line_number)
                prev_sha = sha
            else:
                try:
                    sha_color: str = hash_color[sha]
                except KeyError:
                    sha_color = color_list[counter % len(color_list)]
                    hash_color[sha] = sha_color
                    counter += 1
                raw_author: str = blame["author"]
                if len(raw_author) > self.actual_author_max_len:
                    self.actual_author_max_len = len(raw_author)
                date: str = blame["date"]
                try:
                    if not dim and hash_color[sha] == hash_color[prev_sha]:
                        dim = True
                    else:
                        dim = False
                except KeyError:
                    dim = False
                phantom = (
                    HunkType.NEW_HUNK,
                    line_number,
                    sha_color,
                    sha,
                    raw_author,
                    date,
                    dim,
                )
                prev_sha = sha
            self.raw_list_formatting.append(phantom)

        self.view.settings().set("shas", shas)
        self.phantom_setter()

    # Overrides (BaseBlame) ------------------------------------------------------------

    def _view(self) -> View:
        return self.view

    def extra_cli_args(self, **kwargs):
        return []

    def close_by_user_request(self):
        self.view.run_command("blame_erase_all")

    def rerun(self, **kwargs):
        self.run(None)

    # Overrides end --------------------------------------------------------------------

    def phantom_region(self, line_number: int) -> Region:
        line_begins_pt = self.view.text_point(line_number - 1, 0)
        return sublime.Region(line_begins_pt)

    def settings_for_blame(self):
        self.view.settings().set(
            VIEW_SETTINGS_KEY_RULERS_PREV,
            self.view.settings().get(VIEW_SETTINGS_KEY_RULERS),
        )
        self.view.settings().set(VIEW_SETTINGS_KEY_RULERS, [])

        self.view.settings().set(
            VIEW_SETTINGS_KEY_INDENT_GUIDE_PREV,
            self.view.settings().get(VIEW_SETTINGS_KEY_INDENT_GUIDE),
        )
        self.view.settings().set(VIEW_SETTINGS_KEY_INDENT_GUIDE, False)

        self.view.settings().set(
            VIEW_SETTINGS_KEY_WRAP_PREV,
            self.view.settings().get(VIEW_SETTINGS_KEY_WRAP),
        )
        self.view.settings().set(VIEW_SETTINGS_KEY_WRAP, False)

    def horizontal_scroll_to_limit(self, *, left: bool) -> None:
        x = 0.0 if left else self.view.layout_extent()[0]
        y = self.view.viewport_position()[1]
        # NOTE: The scrolling doesn't seem to work if called inline (or with a 0ms timeout).
        sublime.set_timeout(
            lambda: self.view.set_viewport_position((x, y)),
            self.HORIZONTAL_SCROLL_DELAY_MS,
        )


class BlameEraseAll(sublime_plugin.TextCommand):

    # Overrides begin ------------------------------------------------------------------

    def run(self, edit: Edit) -> None:
        self.view.settings().erase(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED)
        self.view.run_command("blame_restore_rulers")

    # Overrides end --------------------------------------------------------------------


class BlameEraseAllListener(sublime_plugin.ViewEventListener):

    # Overrides begin ------------------------------------------------------------------

    @classmethod
    def is_applicable(cls, settings):
        return settings.get(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED, False)

    def on_modified_async(self):
        self.view.run_command("blame_erase_all")

    # Overrides end --------------------------------------------------------------------


class BlameRestoreRulers(sublime_plugin.TextCommand):

    # Overrides begin ------------------------------------------------------------------

    def run(self, edit: Edit) -> None:
        self.view.settings().set(
            VIEW_SETTINGS_KEY_RULERS,
            self.view.settings().get(VIEW_SETTINGS_KEY_RULERS_PREV),
        )
        self.view.settings().set(
            VIEW_SETTINGS_KEY_INDENT_GUIDE,
            self.view.settings().get(VIEW_SETTINGS_KEY_INDENT_GUIDE_PREV),
        )
        self.view.settings().set(
            VIEW_SETTINGS_KEY_WRAP,
            self.view.settings().get(VIEW_SETTINGS_KEY_WRAP_PREV),
        )

    # Overrides end --------------------------------------------------------------------

import sublime
import sublime_plugin
from typing import List, Union
from sublime import Edit, View, Region

from .base import BaseBlame
from .templates import blame_all_phantom_html_template

VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED = "git-blame-all-displayed"

VIEW_SETTINGS_KEY_RULERS = "rulers"  # A stock ST setting
VIEW_SETTINGS_KEY_RULERS_PREV = "rulers_prev"  # Made up by us

VIEW_SETTINGS_KEY_WRAP = "word_wrap"  # Made up by us
VIEW_SETTINGS_KEY_WRAP_PREV = "word_wrap_prev"  # Made up by us

VIEW_SETTINGS_KEY_INDENT_GUIDE = "draw_indent_guides"  # Made up by us
VIEW_SETTINGS_KEY_INDENT_GUIDE_PREV = "draw_indent_guides_prev"  # Made up by us

dim=") blend(var(--background) 35%)"
color_list = [ "redish", "orangish", "purplish", "yellowish", "greenish", "cyanish", "bluish", "pinkish" ]

class BlameWatcher(BaseBlame, sublime_plugin.ViewEventListener):
    def _view(self) -> View:
        return self.view

    def extra_cli_args(self, **kwargs):
        return []

    def close_by_user_request(self):
        self.view.run_command("blame_erase_all")

    def rerun(self, **kwargs):
        self.run(None)

    def on_modified_async(self):
        self.view.settings().set('shas', [])
        self.view.settings().set('myphantoms', [])


    def on_hover(self, point: int, hover_zone: int) -> None:
        if not self.view.settings().get(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED):
            return
        if hover_zone != sublime.HOVER_TEXT:
            return
        file_name: Union[str,None] = self.view.file_name()
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
        if sha == len(sha) * '0':
            self.view.show_popup("Not committed yet", location=point,flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY)
            return

        try:
            raw_desc = self.get_commit_desc(sha, file_name)
            elems: List[str] = raw_desc.rstrip().split('\n', 1)
            commmit_id = elems[0][7:]
            desc: str = elems[1].replace('\n', '<br>')
            popup_text = f'<body style="padding: 4px; margin: 0; font-family: Inter;"><a href="copy?sha={commmit_id}">{commmit_id}</a><div>{desc}</div></body>'
        except Exception as e:
            self.communicate_error(e)
            return

        self.view.show_popup(popup_text, location=point,flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY, max_width=500, on_navigate=self.handle_phantom_button)


class BlameShowAll(BaseBlame, MyClass, sublime_plugin.TextCommand):
    HORIZONTAL_SCROLL_DELAY_MS = 100

    # Overrides (TextCommand) ----------------------------------------------------------
    def __init__(self, view: View):
        super().__init__(view)
        self.phantom_set = sublime.PhantomSet(self.view, self.phantom_set_key())
        self.pattern = None

    def run(self, edit: Edit):
        if not self.has_suitable_view():
            self.tell_user_to_save()
            return

        self.view.erase_phantoms(self.phantom_set_key())
        phantoms = []  # type: list[sublime.Phantom] # type: ignore[misc]

        myphantoms = self.view.settings().get("myphantoms", [])
        if myphantoms:
            self.phantom_set.update(myphantoms)
            self.view.settings().set(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED, True)
            self.settings_for_blame()
            # Bring the phantoms into view without the user needing to manually scroll left.
            self.horizontal_scroll_to_limit(left=True)

        shas: List[str] = []

        # If they are currently shown, toggle them off and return.
        if self.view.settings().get(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED, False):
            self.view.hide_popup()
            self.phantom_set.update(phantoms)
            self.view.settings().erase(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED)
            self.view.run_command("blame_restore_rulers")
            # Workaround a visible empty space sometimes remaining in the viewport.
            self.horizontal_scroll_to_limit(left=False)
            self.horizontal_scroll_to_limit(left=True)
            return

        try:
            blame_output = self.get_blame_text(self.view.file_name())
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

        max_author_len = max(len(b["author"]) for b in blames)
        if max_author_len > 13:
            max_author_len = 13

        hash_color = {}
        counter = 0
        prev_sha = ''
        modified = False
        for blame in blames:

            line_number = int(blame["line_number"])
            # new commit:
            sha: str = blame["sha"]
            shas.append(sha)
            sha_length=len(sha)
            if sha == sha_length * '0':
                sha_color='foreground) blend(var(--background) 30%)'
            else:
                try:
                    sha_color: str = hash_color[sha]
                except KeyError:
                    sha_color = color_list[counter % len(color_list)]
                    hash_color[sha] = sha_color
                    counter+=1

                if prev_sha != sha:
                    try:
                        if hash_color[sha] == hash_color[prev_sha] and not modified:
                            modified = True
                            sha_color+=dim
                        else:
                            modified = False
                    except KeyError:
                        modified = False
                elif modified:
                    sha_color+=dim

            if prev_sha != sha:
                visualsha=sha
                prev_sha = sha
                if len(blame["author"]) > max_author_len:
                    author = blame["author"][:max_author_len -1] + '…'
                else:
                    author = blame["author"]
                author=author + "&nbsp;" * (max_author_len - len(author))
                date=blame["date"]
            else:
                visualsha="&nbsp;"*sha_length
                author="&nbsp;" * max_author_len
                date="&nbsp;"*10

            phantom = sublime.Phantom(
                self.phantom_region(line_number),
                blame_all_phantom_html_template.format(
                    sha_color=sha_color,
                    sha=sha,
                    visualsha=visualsha,
                    author=author,
                    date=date
                ),
                sublime.LAYOUT_INLINE,
                self.highlight_this_commit,
            )
            phantoms.append(phantom)

        self.phantom_set.update(phantoms)
        # myphantoms = self.view.settings().set("myphantoms", phantoms)
        self.view.settings().set(VIEW_SETTINGS_KEY_PHANTOM_ALL_DISPLAYED, True)
        self.view.settings().set("shas", shas)
        self.settings_for_blame()
        # Bring the phantoms into view without the user needing to manually scroll left.
        self.horizontal_scroll_to_limit(left=True)


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
        sublime.status_message("The git blame result is cleared.")
        self.view.erase_phantoms(BlameShowAll.phantom_set_key())
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

"""Provides a filter for NPK entries in the NPK file list."""

from core.npk.enums import NPKEntryFileCategories
from core.npk.class_types import NPKEntryDataFlags
from gui.utils.npk import get_npk_file, ransack_agent
from gui.widgets.npk_file_list import NPKFileList

class NPKEntryFilter:
    """
    This class is used to filter NPK entries based on given conditions.
    """

    def __init__(self, list_view: NPKFileList):
        self._list_view = list_view
        self.filter_string = ""
        self.filter_type: NPKEntryFileCategories | None = None
        self.include_text = True
        self.include_binary = True
        self.include_slot = True

        self.mesh_biped_head = False

    def apply_filter(self):
        """
        Filters the NPK entries based on the filter string.

        :param npk_entries: List of NPK entries to be filtered.
        :return: Filtered list of NPK entries.
        """
        if self._list_view.disabled():
            return

        model = self._list_view.model()
        npk_file = get_npk_file()
        if not model or not npk_file:
            return

        for row in range(model.rowCount()):
            npk_entry = getattr(npk_file, "peek_entry", npk_file.find_entry_by_id)(row)
            filename_lower = model.get_filename(model.index(row)).lower()
            is_slot_file = getattr(npk_entry, "is_slot_file", False)

            if is_slot_file:
                if not self.include_slot:
                    self._list_view.setRowHidden(row, True)
                    continue
            else:
                if self.include_text == self.include_binary == False:
                    # If both are unchecked, hide all non-slot files
                    self._list_view.setRowHidden(row, True)
                    continue

                if self.include_text != self.include_binary:
                    # If only one is checked, hide the other
                    is_text_file = bool(npk_entry.data_flags & NPKEntryDataFlags.TEXT)
                    if (self.include_text and not is_text_file) or (self.include_binary and is_text_file):
                        self._list_view.setRowHidden(row, True)
                        continue

            # Text filter - quick reject
            if self.filter_string and self.filter_string not in filename_lower:
                self._list_view.setRowHidden(row, True)
                continue

            # Category filtering
            if self.filter_type is None:
                # No filter type set, show all
                show_item = True
            elif self.filter_type == npk_entry.category:
                if self.filter_type == NPKEntryFileCategories.MESH:
                    # Only do the expensive biped head check if needed
                    show_item = not self.mesh_biped_head or ransack_agent(npk_file.find_entry_by_id(row).data, "biped head")
                else:
                    show_item = True
            else:
                show_item = False

            # Apply visibility
            self._list_view.setRowHidden(row, not show_item)

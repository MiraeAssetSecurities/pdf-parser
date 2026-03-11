"""Interactive S3 file browser widget for JupyterLab."""

from __future__ import annotations

import logging
from typing import Callable

import ipywidgets as widgets
from IPython.display import display

from src.s3_handler import S3Handler

logger = logging.getLogger("pdf_parser.s3_browser")


class S3Browser:
    """Interactive S3 file browser widget for JupyterLab notebooks."""

    def __init__(
        self,
        initial_path: str = "s3://",
        on_select_callback: Callable[[str], None] | None = None,
    ):
        """Initialize S3 browser widget.

        Args:
            initial_path: Starting S3 path (default: "s3://")
            on_select_callback: Optional callback function when PDF is selected.
                                Receives selected S3 URI as argument.
        """
        self.s3 = S3Handler()
        self.current_path = initial_path
        self.selected_pdf = None
        self.on_select_callback = on_select_callback

        # UI components
        self._create_widgets()
        self._refresh_display()

    def _create_widgets(self):
        """Create UI widgets."""
        # Path display (editable)
        self.path_input = widgets.Text(
            value=self.current_path,
            description="Current Path:",
            style={"description_width": "100px"},
            layout=widgets.Layout(width="600px"),
        )
        self.path_input.observe(self._on_path_change, names="value")

        # Navigation buttons
        self.go_button = widgets.Button(
            description="Go",
            button_style="primary",
            tooltip="Navigate to entered path",
            layout=widgets.Layout(width="80px"),
        )
        self.go_button.on_click(self._on_go_clicked)

        self.parent_button = widgets.Button(
            description="↑ Parent",
            button_style="info",
            tooltip="Go to parent folder",
            layout=widgets.Layout(width="100px"),
        )
        self.parent_button.on_click(self._on_parent_clicked)

        self.refresh_button = widgets.Button(
            description="↻ Refresh",
            button_style="",
            tooltip="Refresh current view",
            layout=widgets.Layout(width="100px"),
        )
        self.refresh_button.on_click(self._on_refresh_clicked)

        # Navigation bar
        self.nav_bar = widgets.HBox(
            [self.path_input, self.go_button, self.parent_button, self.refresh_button]
        )

        # Status/error output
        self.status_output = widgets.Output(
            layout=widgets.Layout(width="800px", height="30px")
        )

        # Folders section
        self.folders_label = widgets.HTML(value="<b>📁 Folders:</b>")
        self.folders_box = widgets.VBox(layout=widgets.Layout(width="800px"))

        # PDFs section
        self.pdfs_label = widgets.HTML(value="<b>📄 PDF Files:</b>")
        self.pdfs_box = widgets.VBox(layout=widgets.Layout(width="800px"))

        # Selected file display
        self.selected_label = widgets.HTML(
            value="<i>No file selected</i>",
            layout=widgets.Layout(width="800px"),
        )

        # Main container
        self.container = widgets.VBox(
            [
                self.nav_bar,
                self.status_output,
                widgets.HTML(value="<hr>"),
                self.folders_label,
                self.folders_box,
                widgets.HTML(value="<hr>"),
                self.pdfs_label,
                self.pdfs_box,
                widgets.HTML(value="<hr>"),
                self.selected_label,
            ]
        )

    def _refresh_display(self):
        """Refresh the display with current path contents."""
        self.path_input.value = self.current_path

        with self.status_output:
            self.status_output.clear_output()

            try:
                # Handle bucket root (s3://)
                if self.current_path == "s3://":
                    self._display_error("Please enter a bucket name (e.g., s3://my-bucket/)")
                    self.folders_box.children = []
                    self.pdfs_box.children = []
                    return

                # Browse S3 path
                result = self.s3.browse_path(self.current_path)
                folders = result["folders"]
                pdfs = result["pdfs"]

                # Display folders (최대 30개)
                if folders:
                    folder_buttons = []
                    display_folders = folders[:30]
                    for folder in display_folders:
                        btn = widgets.Button(
                            description=f"📁 {folder}",
                            button_style="",
                            layout=widgets.Layout(width="780px", margin="2px"),
                        )
                        btn.folder_name = folder
                        btn.on_click(self._on_folder_clicked)
                        folder_buttons.append(btn)

                    # 더 많은 폴더가 있으면 안내 메시지 추가
                    if len(folders) > 30:
                        info = widgets.HTML(
                            value=f"<i>... 외 {len(folders) - 30}개 폴더 더 있음 (상위 30개만 표시)</i>"
                        )
                        folder_buttons.append(info)

                    self.folders_box.children = folder_buttons
                else:
                    self.folders_box.children = [
                        widgets.HTML(value="<i>No folders</i>")
                    ]

                # Display PDFs (최대 30개)
                if pdfs:
                    pdf_buttons = []
                    display_pdfs = pdfs[:30]
                    for pdf in display_pdfs:
                        btn = widgets.Button(
                            description=f"📄 {pdf}",
                            button_style="success",
                            layout=widgets.Layout(width="780px", margin="2px"),
                        )
                        btn.pdf_name = pdf
                        btn.on_click(self._on_pdf_clicked)
                        pdf_buttons.append(btn)

                    # 더 많은 PDF가 있으면 안내 메시지 추가
                    if len(pdfs) > 30:
                        info = widgets.HTML(
                            value=f"<i>... 외 {len(pdfs) - 30}개 PDF 더 있음 (상위 30개만 표시)</i>"
                        )
                        pdf_buttons.append(info)

                    self.pdfs_box.children = pdf_buttons
                else:
                    self.pdfs_box.children = [widgets.HTML(value="<i>No PDF files</i>")]

                # Update status
                print(f"✓ Found {len(folders)} folders, {len(pdfs)} PDFs")

            except Exception as e:
                self._display_error(f"Error browsing path: {e}")
                self.folders_box.children = []
                self.pdfs_box.children = []

    def _display_error(self, message: str):
        """Display error message."""
        with self.status_output:
            self.status_output.clear_output()
            print(f"❌ {message}")

    def _on_folder_clicked(self, btn):
        """Handle folder button click."""
        folder_name = btn.folder_name
        # Add folder to current path
        if not self.current_path.endswith("/"):
            self.current_path += "/"
        self.current_path += f"{folder_name}/"
        self._refresh_display()

    def _on_pdf_clicked(self, btn):
        """Handle PDF button click."""
        pdf_name = btn.pdf_name
        # Construct full S3 URI
        if not self.current_path.endswith("/"):
            self.current_path += "/"
        self.selected_pdf = f"{self.current_path}{pdf_name}"

        # Update selected label
        self.selected_label.value = f"<b>Selected:</b> <code>{self.selected_pdf}</code>"

        # Call callback if provided
        if self.on_select_callback:
            self.on_select_callback(self.selected_pdf)

    def _on_parent_clicked(self, btn):
        """Handle parent folder button click."""
        if self.current_path == "s3://":
            return

        # Remove trailing slash and go up one level
        path = self.current_path.rstrip("/")
        parts = path.split("/")
        if len(parts) > 2:  # More than s3://bucket
            parent_path = "/".join(parts[:-1]) + "/"
            self.current_path = parent_path
            self._refresh_display()

    def _on_go_clicked(self, btn):
        """Handle Go button click."""
        self.current_path = self.path_input.value
        self._refresh_display()

    def _on_path_change(self, change):
        """Handle path input change."""
        # Update current path when user types
        pass

    def _on_refresh_clicked(self, btn):
        """Handle refresh button click."""
        self._refresh_display()

    def display(self):
        """Display the browser widget."""
        display(self.container)

    def get_selected(self) -> str | None:
        """Get currently selected PDF path.

        Returns:
            S3 URI of selected PDF, or None if nothing selected
        """
        return self.selected_pdf


def create_s3_browser(
    initial_path: str = "s3://",
    on_select: Callable[[str], None] | None = None,
) -> S3Browser:
    """Create and display S3 browser widget.

    Args:
        initial_path: Starting S3 path (default: "s3://")
        on_select: Optional callback function when PDF is selected

    Returns:
        S3Browser instance

    Example:
        >>> def on_pdf_selected(s3_uri):
        ...     print(f"Selected: {s3_uri}")
        ...
        >>> browser = create_s3_browser("s3://my-bucket/pdfs/", on_select=on_pdf_selected)
        >>> # Later retrieve selection:
        >>> selected = browser.get_selected()
    """
    browser = S3Browser(initial_path=initial_path, on_select_callback=on_select)
    browser.display()
    return browser

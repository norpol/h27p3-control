//! Small bindings to the stable GTK4/libadwaita C ABI. All libraries come from
//! Nix; the project deliberately has no downloaded Cargo dependencies.
#![allow(dead_code)]
use std::ffi::{c_char, c_int, c_uint, c_void};
pub type Ptr = *mut c_void;
pub type Callback = Option<unsafe extern "C" fn()>;
extern "C" {
    pub fn adw_application_new(id: *const c_char, flags: c_int) -> Ptr;
    pub fn adw_application_window_new(app: Ptr) -> Ptr;
    pub fn adw_application_window_set_content(window: Ptr, child: Ptr);
    pub fn adw_header_bar_new() -> Ptr;
    pub fn adw_header_bar_set_title_widget(bar: Ptr, title: Ptr);
    pub fn adw_header_bar_pack_end(bar: Ptr, child: Ptr);
    pub fn adw_window_title_new(title: *const c_char, subtitle: *const c_char) -> Ptr;
    pub fn adw_window_title_set_subtitle(title: Ptr, subtitle: *const c_char);
    pub fn adw_preferences_page_new() -> Ptr;
    pub fn adw_preferences_page_add(page: Ptr, group: Ptr);
    pub fn adw_preferences_group_new() -> Ptr;
    pub fn adw_preferences_group_set_title(group: Ptr, title: *const c_char);
    pub fn adw_preferences_group_set_description(group: Ptr, description: *const c_char);
    pub fn adw_preferences_group_add(group: Ptr, child: Ptr);
    pub fn adw_action_row_new() -> Ptr;
    pub fn adw_preferences_row_set_title(row: Ptr, title: *const c_char);
    pub fn adw_preferences_row_set_use_markup(row: Ptr, markup: c_int);
    pub fn adw_action_row_set_subtitle(row: Ptr, subtitle: *const c_char);
    pub fn adw_action_row_set_subtitle_lines(row: Ptr, lines: c_int);
    pub fn adw_action_row_add_suffix(row: Ptr, suffix: Ptr);
    pub fn adw_action_row_set_activatable_widget(row: Ptr, widget: Ptr);
    pub fn adw_style_manager_get_default() -> Ptr;
    pub fn adw_style_manager_set_color_scheme(manager: Ptr, scheme: c_int);
    pub fn adw_style_manager_get_dark(manager: Ptr) -> c_int;
    pub fn adw_style_manager_get_system_supports_color_schemes(manager: Ptr) -> c_int;
    pub fn gtk_window_set_title(window: Ptr, title: *const c_char);
    pub fn gtk_window_set_default_size(window: Ptr, width: c_int, height: c_int);
    pub fn gtk_window_present(window: Ptr);
    pub fn gtk_box_new(orientation: c_int, spacing: c_int) -> Ptr;
    pub fn gtk_box_append(container: Ptr, child: Ptr);
    pub fn gtk_label_new(text: *const c_char) -> Ptr;
    pub fn gtk_label_set_text(label: Ptr, text: *const c_char);
    pub fn gtk_label_set_wrap(label: Ptr, wrap: c_int);
    pub fn gtk_label_set_xalign(label: Ptr, align: f32);
    pub fn gtk_button_new_with_label(label: *const c_char) -> Ptr;
    pub fn gtk_toggle_button_new_with_label(label: *const c_char) -> Ptr;
    pub fn gtk_toggle_button_set_active(button: Ptr, active: c_int);
    pub fn gtk_toggle_button_set_group(button: Ptr, group: Ptr);
    pub fn gtk_button_new_from_icon_name(icon: *const c_char) -> Ptr;
    pub fn gtk_drop_down_new_from_strings(strings: *const *const c_char) -> Ptr;
    pub fn gtk_drop_down_get_selected(dropdown: Ptr) -> c_uint;
    pub fn gtk_drop_down_set_selected(dropdown: Ptr, selected: c_uint);
    pub fn gtk_spin_button_new_with_range(min: f64, max: f64, step: f64) -> Ptr;
    pub fn gtk_spin_button_get_value_as_int(spin: Ptr) -> c_int;
    pub fn gtk_spin_button_set_value(spin: Ptr, value: f64);
    pub fn gtk_spin_button_update(spin: Ptr);
    pub fn gtk_spin_button_set_snap_to_ticks(spin: Ptr, snap: c_int);
    pub fn gtk_stack_new() -> Ptr;
    pub fn gtk_stack_add_titled(
        stack: Ptr,
        child: Ptr,
        name: *const c_char,
        title: *const c_char,
    ) -> Ptr;
    pub fn gtk_stack_set_visible_child_name(stack: Ptr, name: *const c_char);
    pub fn gtk_stack_switcher_new() -> Ptr;
    pub fn gtk_stack_switcher_set_stack(switcher: Ptr, stack: Ptr);
    pub fn gtk_spinner_new() -> Ptr;
    pub fn gtk_spinner_start(spinner: Ptr);
    pub fn gtk_spinner_stop(spinner: Ptr);
    pub fn gtk_widget_set_sensitive(widget: Ptr, sensitive: c_int);
    pub fn gtk_widget_set_visible(widget: Ptr, visible: c_int);
    pub fn gtk_widget_set_hexpand(widget: Ptr, expand: c_int);
    pub fn gtk_widget_set_vexpand(widget: Ptr, expand: c_int);
    pub fn gtk_widget_set_halign(widget: Ptr, align: c_int);
    pub fn gtk_widget_set_valign(widget: Ptr, align: c_int);
    pub fn gtk_widget_set_margin_top(widget: Ptr, margin: c_int);
    pub fn gtk_widget_set_margin_bottom(widget: Ptr, margin: c_int);
    pub fn gtk_widget_set_margin_start(widget: Ptr, margin: c_int);
    pub fn gtk_widget_set_margin_end(widget: Ptr, margin: c_int);
    pub fn gtk_widget_set_tooltip_text(widget: Ptr, text: *const c_char);
    pub fn gtk_widget_add_css_class(widget: Ptr, name: *const c_char);
    pub fn gtk_widget_remove_css_class(widget: Ptr, name: *const c_char);
    pub fn gtk_widget_get_width(widget: Ptr) -> c_int;
    pub fn gtk_widget_get_height(widget: Ptr) -> c_int;
    pub fn gtk_widget_get_native(widget: Ptr) -> Ptr;
    pub fn gtk_widget_paintable_new(widget: Ptr) -> Ptr;
    pub fn gtk_snapshot_new() -> Ptr;
    pub fn gtk_snapshot_free_to_node(snapshot: Ptr) -> Ptr;
    pub fn gdk_paintable_snapshot(paintable: Ptr, snapshot: Ptr, width: f64, height: f64);
    pub fn gtk_native_get_renderer(native: Ptr) -> Ptr;
    pub fn gsk_renderer_render_texture(renderer: Ptr, node: Ptr, viewport: Ptr) -> Ptr;
    pub fn gdk_texture_save_to_png(texture: Ptr, filename: *const c_char) -> c_int;
    pub fn gsk_render_node_unref(node: Ptr);
    pub fn gtk_css_provider_new() -> Ptr;
    pub fn gtk_css_provider_load_from_path(provider: Ptr, path: *const c_char);
    pub fn gtk_css_provider_load_from_string(provider: Ptr, css: *const c_char);
    pub fn gtk_style_context_add_provider_for_display(
        display: Ptr,
        provider: Ptr,
        priority: c_uint,
    );
    pub fn gdk_display_get_default() -> Ptr;
    pub fn g_file_new_for_path(path: *const c_char) -> Ptr;
    pub fn g_file_monitor_directory(
        file: Ptr,
        flags: c_int,
        cancellable: Ptr,
        error: *mut Ptr,
    ) -> Ptr;
    pub fn g_signal_connect_data(
        instance: Ptr,
        signal: *const c_char,
        callback: Callback,
        data: Ptr,
        destroy: Option<unsafe extern "C" fn(Ptr, Ptr)>,
        flags: c_uint,
    ) -> u64;
    pub fn g_timeout_add(
        interval: c_uint,
        callback: Option<unsafe extern "C" fn(Ptr) -> c_int>,
        data: Ptr,
    ) -> c_uint;
    pub fn g_application_run(app: Ptr, argc: c_int, argv: *mut *mut c_char) -> c_int;
    pub fn g_application_quit(app: Ptr);
    pub fn g_object_unref(object: Ptr);
}

mod ffi;
use ffi::*;
use std::collections::HashMap;
use std::ffi::{c_void, CString};
use std::path::PathBuf;
use std::process::Command;
use std::ptr;
use std::sync::mpsc::{self, Receiver, Sender};

fn c(text: &str) -> CString {
    CString::new(text.replace('\0', "")).unwrap()
}

#[derive(Clone, Debug)]
struct Feature {
    key: String,
    group: String,
    label: String,
    min: i32,
    max: i32,
    step: i32,
    options: Vec<(i32, String)>,
    description: String,
}

fn parse_catalog(text: &str) -> Result<Vec<Feature>, String> {
    let mut result = Vec::new();
    for line in text.lines() {
        let p: Vec<_> = line.split('\t').collect();
        if p.len() != 9 || p[0] != "feature" {
            return Err("Invalid control catalog".into());
        }
        let number = |s: &str| {
            s.parse::<i32>()
                .map_err(|_| "Invalid control range".to_string())
        };
        let options = if p[7].is_empty() {
            vec![]
        } else {
            p[7].split('|')
                .map(|entry| {
                    let (value, label) = entry.split_once('=').ok_or("Invalid option")?;
                    Ok((number(value)?, label.to_string()))
                })
                .collect::<Result<Vec<_>, String>>()?
        };
        let f = Feature {
            key: p[1].into(),
            group: p[2].into(),
            label: p[3].into(),
            min: number(p[4])?,
            max: number(p[5])?,
            step: number(p[6])?,
            options,
            description: p[8].into(),
        };
        if f.step <= 0 || f.min > f.max || f.options.iter().any(|(v, _)| *v < f.min || *v > f.max) {
            return Err("Invalid control range".into());
        }
        if result.iter().any(|old: &Feature| old.key == f.key) {
            return Err("Duplicate control".into());
        }
        result.push(f);
    }
    if result.is_empty() {
        return Err("Empty control catalog".into());
    }
    Ok(result)
}

#[derive(Default, Debug)]
struct Snapshot {
    values: HashMap<String, i32>,
    errors: HashMap<String, String>,
    mode: String,
    firmware: String,
}

fn parse_snapshot(text: &str, catalog: &[Feature]) -> Result<Snapshot, String> {
    let mut snapshot = Snapshot::default();
    for line in text.lines() {
        let p: Vec<_> = line.split('\t').collect();
        match p.as_slice() {
            ["mode", value] if *value == "work" || *value == "game" => {
                snapshot.mode = value.to_string()
            }
            ["device", "KTC H27P3", version, _, _] => snapshot.firmware = version.to_string(),
            ["value", key, value] => {
                let f = catalog
                    .iter()
                    .find(|f| f.key == *key)
                    .ok_or("Unknown control in monitor response")?;
                let value: i32 = value.parse().map_err(|_| "Invalid control value")?;
                if value < f.min
                    || value > f.max
                    || (value - f.min) % f.step != 0
                    || (!f.options.is_empty() && !f.options.iter().any(|(v, _)| *v == value))
                {
                    return Err(format!("Unexpected value for {}", f.label));
                }
                snapshot.values.insert(key.to_string(), value);
            }
            ["unavailable", key, error] => {
                snapshot.errors.insert(key.to_string(), error.to_string());
            }
            ["capture", _] => {}
            _ => return Err("Unexpected monitor service response".into()),
        }
    }
    if snapshot.mode.is_empty() || snapshot.firmware.is_empty() {
        return Err("Incomplete monitor response".into());
    }
    if snapshot.values.is_empty() {
        return Err("No controls responded. Wake the screen and refresh.".into());
    }
    Ok(snapshot)
}

fn backend_path() -> PathBuf {
    std::env::var_os("H27P3_BACKEND")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("backend/control.py"))
}

fn backend(args: &[String]) -> Result<String, String> {
    let python = std::env::var_os("H27P3_PYTHON").unwrap_or_else(|| "python3".into());
    let output = Command::new(python)
        .arg(backend_path())
        .args(args)
        .output()
        .map_err(|e| format!("Could not start monitor service: {e}"))?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

#[derive(Clone)]
enum Action {
    Refresh,
    Apply(String),
    Work,
    Game,
}
struct Row {
    feature: Feature,
    row: Ptr,
    input: Ptr,
}
struct ActionData {
    state: *mut State,
    action: Action,
}
struct State {
    app: Ptr,
    window: Ptr,
    title: Ptr,
    status: Ptr,
    refresh: Ptr,
    spinner: Ptr,
    work: Ptr,
    game: Ptr,
    mode_subtitle: Ptr,
    stack: Ptr,
    catalog: Vec<Feature>,
    rows: Vec<Row>,
    snapshot: Snapshot,
    busy: bool,
    valid: bool,
    tx: Sender<Result<String, String>>,
    rx: Receiver<Result<String, String>>,
    demo: bool,
    smoke: bool,
    smoke_stage: u8,
    screenshot: Option<String>,
    preview_attempts: u8,
    exit_code: i32,
    preview_page: String,
    preview_theme: i32,
    css_provider: Ptr,
    css_path: PathBuf,
    css_reload_pending: bool,
}

unsafe fn connect(instance: Ptr, signal: &str, callback: Callback, data: Ptr) {
    g_signal_connect_data(instance, c(signal).as_ptr(), callback, data, None, 0);
}

unsafe extern "C" fn free_action(data: Ptr, _: Ptr) {
    drop(Box::from_raw(data as *mut ActionData));
}

unsafe fn action_button(button: Ptr, state: *mut State, action: Action) {
    let data = Box::into_raw(Box::new(ActionData { state, action }));
    let callback =
        std::mem::transmute::<unsafe extern "C" fn(Ptr, Ptr), unsafe extern "C" fn()>(clicked);
    g_signal_connect_data(
        button,
        c("clicked").as_ptr(),
        Some(callback),
        data.cast(),
        Some(free_action),
        0,
    );
}

fn demo_response(catalog: &[Feature], old: &Snapshot, args: &[String]) -> String {
    let mut values = old.values.clone();
    if values.is_empty() {
        for f in catalog {
            let value = match f.key.as_str() {
                "brightness" => 30,
                "volume" => 97,
                "sharpness" => 5,
                "gamma" => 3,
                "color-temp" | "overdrive" | "adaptive-sync" | "hdr" | "crosshair"
                | "osd-transparency" => 1,
                "osd-timeout" => 15,
                "osd-style" => 3,
                "contrast" | "black-equalizer" | "red-gain" | "green-gain" | "blue-gain"
                | "osd-h" | "osd-v" => 50,
                _ if f.group == "Six-axis color" => 50,
                _ => f.min,
            };
            values.insert(f.key.clone(), value);
        }
    }
    let mut mode = if old.mode.is_empty() {
        "work".to_string()
    } else {
        old.mode.clone()
    };
    if args[0] == "set" {
        let value = args[2].parse::<i32>().unwrap();
        if ["crosshair", "fps", "timer"].contains(&args[1].as_str()) && value != 0 {
            for key in ["crosshair", "fps", "timer"] {
                values.insert(key.into(), 0);
            }
        }
        values.insert(args[1].clone(), value);
    } else if args[0] == "mode" {
        mode = args[1].clone();
    }
    let mut output = format!("device\tKTC H27P3\tEUR-1.0.1\tKSDA25040451\t15\nmode\t{mode}\n");
    for (key, value) in values {
        output.push_str(&format!("value\t{key}\t{value}\n"));
    }
    output
}

impl State {
    unsafe fn label(&self, text: &str) {
        gtk_label_set_text(self.status, c(text).as_ptr());
    }

    unsafe fn update_sensitivity(&self) {
        gtk_widget_set_sensitive(self.refresh, (!self.busy).into());
        gtk_widget_set_sensitive(self.work, (!self.busy && self.valid).into());
        gtk_widget_set_sensitive(self.game, (!self.busy && self.valid).into());
        for row in &self.rows {
            let key = row.feature.key.as_str();
            let mut reason = self.snapshot.errors.get(key).cloned();
            let color = row.feature.group == "Six-axis color"
                || [
                    "gamma",
                    "color-temp",
                    "red-gain",
                    "green-gain",
                    "blue-gain",
                    "blue-light",
                ]
                .contains(&key);
            if color && self.snapshot.values.get("color-space") != Some(&0) {
                reason = Some("Select Native color space to adjust this control.".into());
            } else if key.ends_with("-gain") && self.snapshot.values.get("color-temp") != Some(&4) {
                reason = Some("Select User color temperature to adjust this channel.".into());
            } else if key == "brightness" && self.snapshot.values.get("dcr") == Some(&1) {
                reason = Some("Turn off Dynamic contrast to adjust brightness.".into());
            }
            let available = self.valid
                && !self.busy
                && self.snapshot.values.contains_key(key)
                && reason.is_none();
            gtk_widget_set_sensitive(row.row, available.into());
            adw_action_row_set_subtitle(
                row.row,
                c(reason.as_deref().unwrap_or(&row.feature.description)).as_ptr(),
            );
        }
    }

    unsafe fn start(&mut self, args: Vec<String>, message: &str) {
        if self.busy {
            return;
        }
        self.busy = true;
        self.label(message);
        gtk_widget_remove_css_class(self.status, c("error").as_ptr());
        gtk_spinner_start(self.spinner);
        self.update_sensitivity();
        let tx = self.tx.clone();
        let demo = self
            .demo
            .then(|| demo_response(&self.catalog, &self.snapshot, &args));
        std::thread::spawn(move || {
            let result = match demo {
                Some(response) => {
                    std::thread::sleep(std::time::Duration::from_millis(180));
                    Ok(response)
                }
                None => backend(&args),
            };
            let _ = tx.send(result);
        });
    }

    unsafe fn populate(&mut self) {
        for row in &self.rows {
            if let Some(value) = self.snapshot.values.get(&row.feature.key) {
                if row.feature.options.is_empty() {
                    gtk_spin_button_set_value(row.input, *value as f64);
                } else if let Some(index) = row.feature.options.iter().position(|(v, _)| v == value)
                {
                    gtk_drop_down_set_selected(row.input, index as u32);
                }
            }
        }
        let description = if self.snapshot.mode == "work" {
            "5K · 60 Hz"
        } else {
            "1440p · 120 Hz"
        };
        adw_action_row_set_subtitle(
            self.mode_subtitle,
            c(&format!(
                "{description} · switching briefly blanks the screen"
            ))
            .as_ptr(),
        );
        adw_window_title_set_subtitle(
            self.title,
            c(&format!("{description} · {}", self.snapshot.firmware)).as_ptr(),
        );
        for (button, active) in [
            (self.work, self.snapshot.mode == "work"),
            (self.game, self.snapshot.mode == "game"),
        ] {
            gtk_toggle_button_set_active(button, active.into());
        }
    }
}

unsafe extern "C" fn clicked(_: Ptr, data: Ptr) {
    let data = &*(data as *const ActionData);
    let state = &mut *data.state;
    if state.busy {
        return;
    }
    match &data.action {
        Action::Refresh => state.start(vec!["status".into()], "Reading monitor settings…"),
        Action::Work | Action::Game => {
            let target = if matches!(data.action, Action::Work) {
                "work"
            } else {
                "game"
            };
            if state.snapshot.mode == target {
                state.populate();
                return;
            }
            state.start(
                vec!["mode".into(), target.into()],
                "Switching display mode… the screen will briefly go blank.",
            );
        }
        Action::Apply(key) => {
            if !state.valid {
                return;
            }
            if let Some(row) = state.rows.iter().find(|row| &row.feature.key == key) {
                let value = if row.feature.options.is_empty() {
                    gtk_spin_button_update(row.input);
                    gtk_spin_button_get_value_as_int(row.input)
                } else {
                    let index = gtk_drop_down_get_selected(row.input) as usize;
                    match row.feature.options.get(index) {
                        Some((v, _)) => *v,
                        None => return,
                    }
                };
                let message = format!("Applying {}…", row.feature.label.to_lowercase());
                state.start(vec!["set".into(), key.clone(), value.to_string()], &message);
            }
        }
    }
}

unsafe extern "C" fn poll(data: Ptr) -> i32 {
    let state = &mut *(data as *mut State);
    if let Ok(result) = state.rx.try_recv() {
        state.busy = false;
        gtk_spinner_stop(state.spinner);
        match result.and_then(|text| parse_snapshot(&text, &state.catalog)) {
            Ok(snapshot) => {
                state.snapshot = snapshot;
                state.valid = true;
                state.populate();
                let count = state.snapshot.values.len();
                state.label(&if state.demo {
                    format!("Demo · {count} simulated controls; no monitor commands sent")
                } else {
                    format!("Connected · {count} controls read from the monitor")
                });
            }
            Err(error) => {
                state.valid = false;
                state.label(&format!("{error}\nRefresh to read the current settings."));
                gtk_widget_add_css_class(state.status, c("error").as_ptr());
            }
        }
        state.update_sensitivity();
        if state.screenshot.is_some() {
            gtk_stack_set_visible_child_name(state.stack, c(&state.preview_page).as_ptr());
            g_timeout_add(500, Some(save_preview), data);
        }
        if state.smoke {
            assert!(state.valid, "Demo service returned invalid data");
            match state.smoke_stage {
                0 => {
                    state.smoke_stage = 1;
                    gtk_stack_set_visible_child_name(state.stack, c("Gaming").as_ptr());
                    state.start(
                        vec!["set".into(), "fps".into(), "1".into()],
                        "Testing overlay selection…",
                    );
                }
                1 => {
                    assert_eq!(state.snapshot.values.get("fps"), Some(&1));
                    assert_eq!(state.snapshot.values.get("crosshair"), Some(&0));
                    let manager = adw_style_manager_get_default();
                    adw_style_manager_set_color_scheme(manager, 4); // FORCE_DARK
                    assert_ne!(adw_style_manager_get_dark(manager), 0);
                    adw_style_manager_set_color_scheme(manager, 1); // FORCE_LIGHT
                    assert_eq!(adw_style_manager_get_dark(manager), 0);
                    adw_style_manager_set_color_scheme(manager, 0); // Follow system.
                    println!("GTK smoke test passed: all rows, async updates, overlay selection, light/dark.");
                    g_application_quit(state.app);
                }
                _ => {}
            }
        }
    }
    1
}

// Captures only this application's widget tree. No desktop screenshot or
// clipboard access; --screenshot always runs in demo mode with no monitor I/O.
unsafe fn preview_not_ready(state: &mut State) -> i32 {
    // The compositor may not have supplied a frame when the first timer fires.
    // Retry the 500 ms timer, but stop if the window never becomes drawable.
    state.preview_attempts += 1;
    if state.preview_attempts < 20 {
        return 1;
    }
    eprintln!("Could not capture preview: the window did not render within 10 seconds.");
    state.exit_code = 1;
    g_application_quit(state.app);
    0
}

unsafe extern "C" fn save_preview(data: Ptr) -> i32 {
    let state = &mut *(data as *mut State);
    if let Some(path) = state.screenshot.clone() {
        let width = gtk_widget_get_width(state.window);
        let height = gtk_widget_get_height(state.window);
        if width <= 0 || height <= 0 {
            return preview_not_ready(state);
        }
        let paintable = gtk_widget_paintable_new(state.window);
        let snapshot = gtk_snapshot_new();
        gdk_paintable_snapshot(paintable, snapshot, width as f64, height as f64);
        let node = gtk_snapshot_free_to_node(snapshot);
        g_object_unref(paintable);
        if node.is_null() {
            return preview_not_ready(state);
        }
        let native = gtk_widget_get_native(state.window);
        let renderer = if native.is_null() {
            ptr::null_mut()
        } else {
            gtk_native_get_renderer(native)
        };
        if renderer.is_null() {
            gsk_render_node_unref(node);
            return preview_not_ready(state);
        }
        let texture = gsk_renderer_render_texture(renderer, node, ptr::null_mut());
        gsk_render_node_unref(node);
        if texture.is_null() {
            return preview_not_ready(state);
        }
        let saved = gdk_texture_save_to_png(texture, c(&path).as_ptr()) != 0;
        g_object_unref(texture);
        state.screenshot = None;
        if saved {
            println!("Saved app preview: {path}");
        } else {
            eprintln!("Could not write preview to {path}");
            state.exit_code = 1;
        }
        g_application_quit(state.app);
    }
    0
}

unsafe extern "C" fn reload_desktop_css(data: Ptr) -> i32 {
    let state = &mut *(data as *mut State);
    state.css_reload_pending = false;
    if state.css_path.is_file() {
        gtk_css_provider_load_from_path(
            state.css_provider,
            c(&state.css_path.to_string_lossy()).as_ptr(),
        );
    } else {
        gtk_css_provider_load_from_string(state.css_provider, c("").as_ptr());
    }
    0
}

unsafe extern "C" fn desktop_css_changed(_: Ptr, _: Ptr, _: Ptr, _: i32, data: Ptr) {
    let state = &mut *(data as *mut State);
    if !state.css_reload_pending {
        state.css_reload_pending = true;
        g_timeout_add(300, Some(reload_desktop_css), data);
    }
}

unsafe fn follow_desktop_css(state: *mut State) {
    let config = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
        });
    let directory = config.join("gtk-4.0");
    if !directory.is_dir() {
        return;
    }
    (*state).css_path = directory.join("gtk.css");
    (*state).css_provider = gtk_css_provider_new();
    // GTK's initial user CSS can contain fixed Noctalia colors. Load the same
    // user stylesheet at one priority higher, and watch its directory (including
    // imported files) so atomic theme updates replace cached colors live.
    gtk_style_context_add_provider_for_display(
        gdk_display_get_default(),
        (*state).css_provider,
        801,
    );
    reload_desktop_css(state.cast());
    let file = g_file_new_for_path(c(&directory.to_string_lossy()).as_ptr());
    let monitor = g_file_monitor_directory(file, 0, ptr::null_mut(), ptr::null_mut());
    g_object_unref(file);
    if !monitor.is_null() {
        connect(
            monitor,
            "changed",
            Some(std::mem::transmute::<
                unsafe extern "C" fn(Ptr, Ptr, Ptr, i32, Ptr),
                unsafe extern "C" fn(),
            >(desktop_css_changed)),
            state.cast(),
        );
        // Keep the monitor alive for the application's lifetime, like State.
    }
}

unsafe fn row(title: &str, subtitle: &str) -> Ptr {
    let row = adw_action_row_new();
    adw_preferences_row_set_use_markup(row, 0);
    adw_preferences_row_set_title(row, c(title).as_ptr());
    adw_action_row_set_subtitle(row, c(subtitle).as_ptr());
    adw_action_row_set_subtitle_lines(row, 3);
    row
}

unsafe extern "C" fn activate(_: Ptr, data: Ptr) {
    let state = &mut *(data as *mut State);
    if !state.window.is_null() {
        gtk_window_present(state.window);
        return;
    }
    adw_style_manager_set_color_scheme(adw_style_manager_get_default(), state.preview_theme);
    follow_desktop_css(state as *mut State);
    state.window = adw_application_window_new(state.app);
    gtk_window_set_title(state.window, c("H27P3 Control").as_ptr());
    gtk_window_set_default_size(state.window, 880, 840);
    let root = gtk_box_new(1, 0);
    adw_application_window_set_content(state.window, root);
    let header = adw_header_bar_new();
    state.title = adw_window_title_new(c("H27P3 Control").as_ptr(), c("KTC monitor").as_ptr());
    adw_header_bar_set_title_widget(header, state.title);
    state.refresh = gtk_button_new_from_icon_name(c("view-refresh-symbolic").as_ptr());
    gtk_widget_set_tooltip_text(state.refresh, c("Read current monitor settings").as_ptr());
    action_button(state.refresh, data.cast(), Action::Refresh);
    adw_header_bar_pack_end(header, state.refresh);
    state.spinner = gtk_spinner_new();
    adw_header_bar_pack_end(header, state.spinner);
    gtk_box_append(root, header);

    let mode_group = adw_preferences_group_new();
    for (function, margin) in [
        (
            gtk_widget_set_margin_start as unsafe extern "C" fn(Ptr, i32),
            24,
        ),
        (gtk_widget_set_margin_end, 24),
        (gtk_widget_set_margin_top, 12),
        (gtk_widget_set_margin_bottom, 12),
    ] {
        function(mode_group, margin);
    }
    state.mode_subtitle = row("Display mode", "Reading monitor…");
    let modes = gtk_box_new(0, 0);
    gtk_widget_add_css_class(modes, c("linked").as_ptr());
    gtk_widget_set_valign(modes, 3);
    state.work = gtk_toggle_button_new_with_label(c("Work · 5K / 60").as_ptr());
    state.game = gtk_toggle_button_new_with_label(c("Game · 2K / 120").as_ptr());
    gtk_toggle_button_set_group(state.game, state.work);
    action_button(state.work, data.cast(), Action::Work);
    action_button(state.game, data.cast(), Action::Game);
    gtk_box_append(modes, state.work);
    gtk_box_append(modes, state.game);
    adw_action_row_add_suffix(state.mode_subtitle, modes);
    adw_preferences_group_add(mode_group, state.mode_subtitle);
    gtk_box_append(root, mode_group);

    state.stack = gtk_stack_new();
    gtk_widget_set_vexpand(state.stack, 1);
    let switcher = gtk_stack_switcher_new();
    gtk_stack_switcher_set_stack(switcher, state.stack);
    gtk_widget_set_halign(switcher, 3);
    gtk_widget_set_margin_bottom(switcher, 6);
    gtk_box_append(root, switcher);
    gtk_box_append(root, state.stack);
    for group_name in [
        "Display",
        "Gaming",
        "Color",
        "Six-axis color",
        "Audio & OSD",
    ] {
        let page = adw_preferences_page_new();
        let group = adw_preferences_group_new();
        adw_preferences_group_set_title(group, c(&group_name.replace('&', "&amp;")).as_ptr());
        adw_preferences_group_set_description(
            group,
            c(if group_name == "Gaming" {
                "Choose an overlay, then Apply. Crosshair, timer, and counter replace one another."
            } else {
                "Edit a value, then Apply. Values are read back from the monitor."
            })
            .as_ptr(),
        );
        adw_preferences_page_add(page, group);
        for feature in state.catalog.iter().filter(|f| f.group == group_name) {
            let item = row(&feature.label, &feature.description);
            let input = if feature.options.is_empty() {
                let input = gtk_spin_button_new_with_range(
                    feature.min as f64,
                    feature.max as f64,
                    feature.step as f64,
                );
                gtk_spin_button_set_snap_to_ticks(input, 1);
                input
            } else {
                let strings: Vec<CString> =
                    feature.options.iter().map(|(_, label)| c(label)).collect();
                let mut pointers: Vec<_> = strings.iter().map(|s| s.as_ptr()).collect();
                pointers.push(ptr::null());
                gtk_drop_down_new_from_strings(pointers.as_ptr())
            };
            gtk_widget_set_valign(input, 3);
            gtk_widget_set_tooltip_text(input, c(&feature.label).as_ptr());
            adw_action_row_set_activatable_widget(item, input);
            adw_action_row_add_suffix(item, input);
            let apply = gtk_button_new_with_label(c("Apply").as_ptr());
            gtk_widget_set_valign(apply, 3);
            gtk_widget_set_tooltip_text(
                apply,
                c(&format!("Apply {}", feature.label.to_lowercase())).as_ptr(),
            );
            action_button(apply, data.cast(), Action::Apply(feature.key.clone()));
            adw_action_row_add_suffix(item, apply);
            adw_preferences_group_add(group, item);
            state.rows.push(Row {
                feature: feature.clone(),
                row: item,
                input,
            });
        }
        let title = if group_name == "Six-axis color" {
            "Six-axis"
        } else {
            group_name
        };
        gtk_stack_add_titled(state.stack, page, c(group_name).as_ptr(), c(title).as_ptr());
    }
    let about = adw_preferences_page_new();
    let support = adw_preferences_group_new();
    adw_preferences_group_set_title(support, c("More monitor features").as_ptr());
    adw_preferences_group_set_description(
        support,
        c("Some settings still need the physical monitor menu.").as_ptr(),
    );
    for (title, detail) in [
        ("Night Vision and Eagle Eye", "No confirmed command has been identified for this firmware yet."),
        ("Custom crosshair color and position", "This firmware does not answer the app’s custom color or position controls. Six built-in styles are available."),
        ("MPRT and RGB lighting", "Not supported by this H27P3 model."),
        ("Input, USB, language, and picture presets", "Use the monitor menu. The command mappings are not yet confirmed for this unit."),
        ("Appearance", "Follows your desktop’s light or dark preference automatically."),
        ("Firmware", "H27P3 EUR-1.0.1 / KSDA25040451. Controls are enabled only after reading this monitor."),
    ] { adw_preferences_group_add(support, row(title, detail)); }
    adw_preferences_page_add(about, support);
    gtk_stack_add_titled(state.stack, about, c("About").as_ptr(), c("More").as_ptr());

    state.status = gtk_label_new(c("Connecting…").as_ptr());
    gtk_label_set_wrap(state.status, 1);
    gtk_widget_add_css_class(state.status, c("dim-label").as_ptr());
    gtk_widget_set_margin_start(state.status, 24);
    gtk_widget_set_margin_end(state.status, 24);
    gtk_widget_set_margin_top(state.status, 10);
    gtk_widget_set_margin_bottom(state.status, 14);
    gtk_box_append(root, state.status);
    state.start(vec!["status".into()], "Reading monitor settings…");
    g_timeout_add(60, Some(poll), data);
    gtk_window_present(state.window);
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut demo = false;
    let mut smoke = false;
    let mut screenshot = None;
    let mut preview_page = "Display".to_string();
    let mut preview_theme = 0;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--demo" => demo = true,
            "--smoke-test" => {
                smoke = true;
                demo = true;
            }
            "--screenshot" if index + 1 < args.len() => {
                index += 1;
                screenshot = Some(args[index].clone());
                demo = true;
            }
            "--preview-page" if index + 1 < args.len() => {
                index += 1;
                preview_page = args[index].clone();
                demo = true;
            }
            "--preview-dark" => {
                preview_theme = 4;
                demo = true;
            }
            "--preview-light" => {
                preview_theme = 1;
                demo = true;
            }
            _ => {
                eprintln!("Usage: h27p3-control [--demo] [--smoke-test] [--screenshot FILE] [--preview-page PAGE] [--preview-dark|--preview-light]");
                std::process::exit(2);
            }
        }
        index += 1;
    }
    let catalog = backend(&["catalog".into()])
        .and_then(|text| parse_catalog(&text))
        .unwrap_or_else(|error| {
            eprintln!("{error}");
            std::process::exit(1)
        });
    let (tx, rx) = mpsc::channel();
    unsafe {
        let app = adw_application_new(
            c(if demo {
                "com.local.H27P3Control.Demo"
            } else {
                "com.local.H27P3Control"
            })
            .as_ptr(),
            if demo { 32 } else { 0 },
        );
        // GTK callbacks use this state only on the main thread. Workers own only
        // strings and a Sender. State remains alive until the process exits.
        let state = Box::into_raw(Box::new(State {
            app,
            window: ptr::null_mut(),
            title: ptr::null_mut(),
            status: ptr::null_mut(),
            refresh: ptr::null_mut(),
            spinner: ptr::null_mut(),
            work: ptr::null_mut(),
            game: ptr::null_mut(),
            mode_subtitle: ptr::null_mut(),
            stack: ptr::null_mut(),
            catalog,
            rows: vec![],
            snapshot: Snapshot::default(),
            busy: false,
            valid: false,
            tx,
            rx,
            demo,
            smoke,
            smoke_stage: 0,
            screenshot,
            preview_attempts: 0,
            exit_code: 0,
            preview_page,
            preview_theme,
            css_provider: ptr::null_mut(),
            css_path: PathBuf::new(),
            css_reload_pending: false,
        }));
        connect(
            app,
            "activate",
            Some(std::mem::transmute::<
                unsafe extern "C" fn(Ptr, Ptr),
                unsafe extern "C" fn(),
            >(activate)),
            state.cast::<c_void>(),
        );
        let code = g_application_run(app, 0, ptr::null_mut());
        g_object_unref(app);
        std::process::exit(if code != 0 { code } else { (*state).exit_code });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn catalog() -> Vec<Feature> {
        parse_catalog("feature\tcrosshair\tGaming\tCrosshair\t0\t6\t1\t0=Off|1=Style 1\tOverlay\n")
            .unwrap()
    }
    #[test]
    fn invalid_monitor_values_are_not_displayed_as_valid() {
        let data = "device\tKTC H27P3\tEUR-1.0.1\tbuild\t15\nmode\twork\nvalue\tcrosshair\t7\n";
        assert!(parse_snapshot(data, &catalog()).is_err());
        assert!(parse_snapshot(&data.replace("\t7\n", "\t1\n"), &catalog()).is_ok());
    }
    #[test]
    fn missing_identity_and_unknown_controls_fail() {
        assert!(parse_snapshot("value\tcrosshair\t1\n", &catalog()).is_err());
        assert!(parse_snapshot(
            "device\tKTC H27P3\tv\tb\t15\nmode\twork\nvalue\tunknown\t1\n",
            &catalog()
        )
        .is_err());
    }
    #[test]
    fn malformed_catalogs_are_rejected() {
        assert!(parse_catalog("").is_err());
        assert!(parse_catalog("feature\tx\tg\tl\t0\t5\t0\t\td\n").is_err());
    }
}

// No console window on Windows (the backend's output goes to the log file, not to a terminal).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    mtgdeck_desktop_lib::run()
}

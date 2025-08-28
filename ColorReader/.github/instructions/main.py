# main.py
import pygame
import logging
from constants import *
from ui import UI
from ble_manager import BleManager

def main():
    open("traffic_log.txt", "w").close()
    logging.basicConfig(filename="traffic_log.txt", level=logging.INFO, format="%(asctime)s - %(message)s")

    ui = UI(log_fn=lambda m: (ui.log(m), logging.info(m)))
    mgr = BleManager(log_fn=lambda m: (ui.log(m), logging.info(m)))
    mgr.start_loop()

    # actions
    def do_scan():
        def _done(fut):
            ui.set_devices(fut.result())
        mgr.submit(mgr.scan()).add_done_callback(_done)

    def do_connect():
        if ui.selected_device_idx < 0 or ui.selected_device_idx >= len(ui.devices):
            ui.log("Select a device first.")
            return
        sel = ui.devices[ui.selected_device_idx]
        address = sel.split("(")[-1].strip(")")
        def _after(_):
            ui.set_chars(mgr.discovered_chars[:])
        mgr.submit(mgr.connect(address)).add_done_callback(_after)

    def do_read():
        if ui.selected_char_idx < 0 or ui.selected_char_idx >= len(mgr.discovered_chars):
            ui.log("Select a characteristic in the right list.")
            return
        uuid = mgr.discovered_chars[ui.selected_char_idx][0]
        mgr.submit(mgr.read_gatt_char(uuid))

    def do_write(payload: bytes):
        if ui.selected_char_idx < 0 or ui.selected_char_idx >= len(mgr.discovered_chars):
            ui.log("Select a characteristic in the right list.")
            return
        uuid, props = mgr.discovered_chars[ui.selected_char_idx]
        use_resp = ("write-without-response" not in set(props))
        mgr.submit(mgr.write_gatt_char(uuid, payload, response=use_resp))

    def do_notify_on():
        if ui.selected_char_idx < 0 or ui.selected_char_idx >= len(mgr.discovered_chars):
            ui.log("Select a characteristic in the right list.")
            return
        uuid = mgr.discovered_chars[ui.selected_char_idx][0]
        mgr.submit(mgr.set_notify(uuid, True))

    def do_notify_off():
        if ui.selected_char_idx < 0 or ui.selected_char_idx >= len(mgr.discovered_chars):
            ui.log("Select a characteristic in the right list.")
            return
        uuid = mgr.discovered_chars[ui.selected_char_idx][0]
        mgr.submit(mgr.set_notify(uuid, False))

    def do_read_all():   mgr.submit(mgr.read_all_readable())
    def do_probe():      mgr.submit(mgr.probe_for_measure())
    def do_measure():    mgr.submit(mgr.attempt_measure())
    def do_brute():      mgr.submit(mgr.brute_force_triggers())
    def do_brute_alt():  mgr.submit(mgr.brute_force_alt_on_0a19())
    def do_dump_cccd():  mgr.submit(mgr.dump_descriptors())

    ui.bind(
        scan_cb=do_scan,
        connect_cb=do_connect,
        read_cb=do_read,
        write_cb=do_write,
        notify_on_cb=do_notify_on,
        notify_off_cb=do_notify_off,
        read_all_cb=do_read_all,
        probe_cb=do_probe,
        measure_cb=do_measure,
        brute_cb=do_brute,
        brute_alt_cb=do_brute_alt,
        dump_cccd_cb=do_dump_cccd
    )

    running = True
    clock = pygame.time.Clock()
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            else:
                ui.handle_event(event)
        ui.draw()
        clock.tick(60)

    try:
        if mgr.client:
            mgr.submit(mgr.client.disconnect())
    except Exception:
        pass
    mgr.stop_loop()
    pygame.quit()

if __name__ == "__main__":
    main()

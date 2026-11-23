# Public alpha release checklist

- [ ] Verify the app launches on the target Raspberry Pi + display.
- [ ] Verify the Moonraker connection and printer detection.
- [ ] Test without an active print: homing, fans, heaters, manual extrusion, limits, overrides, LED effects, macros and console.
- [ ] Verify config backup/preservation during update.
- [ ] Confirm `~/printer_data/config/veloxscreen.conf` is loaded.
- [ ] Confirm service restart in Mainsail works after a reboot.
- [ ] If testing service switching, migrate old tty1 autostart blocks and verify KlipperScreen can be restored.
- [ ] Review README, screenshots and config for personal information/API tokens.
- [ ] Confirm default UI language is English and report untranslated labels.
- [ ] Leave GitHub Issues enabled and explain this is an experimental alpha.
- [ ] Release with an **alpha/pre-release** tag, not as stable production software.

Software syntax checks alone do **not** substitute for real hardware testing.

- [ ] Test 2.8-inch / 320x240 touch layouts on real hardware (experimental).
- [ ] Test Moonraker-managed update and managed_service restart on an idle printer.
- [ ] Confirm `veloxscreen.conf` and custom logo remain untouched after updates.

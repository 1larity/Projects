---
applyTo: '**'
---
Provide project context and coding guidelines that AI should follow when generating code, answering questions, or reviewing changes.

1. **Project Context**:
   - This project is a Color Reader application built with Python.
   - The main functionality is to read and process color data from the Datacolor ColorReader Pro.
   - The codebase is primarily written in Python.
   - We need to interact with the reader's Bluetooth communications.

   Follow these instructions to create Python scripts that reverse-engineer and read data from a Datacolor ColorReader over Bluetooth Low Energy (BLE):

   1. **Environment**:
      - Use Python 3.9+ in a virtual environment.
      - Install `bleak`: `pip install bleak`.
      - Ensure PC Bluetooth supports BLE:
        - **Windows 10/11**: Native BLE support.
        - **Linux**: Install BlueZ.
        - **macOS**: Version 10.13+.

   2. **Scan for Device**:
      - Write `scan.py` using `bleak.BleakScanner.discover()`.
      - Filter devices with names containing "color" or "datacolor".
      - Output the name and address.

   3. **Dump GATT**:
      - Write `dump_gatt.py` to connect with `bleak.BleakClient(address)`.
      - Enumerate services, characteristics, and descriptors.
      - Record UUIDs:
        - Battery service: `0000180F`.
        - Battery level: `00002A19`.
        - Vendor-specific UUIDs and any "notify" or "write" properties.

   4. **Subscribe to Notifications**:
      - Write `listen.py` to subscribe to all notifiable characteristics with `start_notify()`.
      - Press the ColorReader button and log hex data.

   5. **Read Known Values**:
      - Write `read_common.py` to read battery level and device info.

   6. **Test Writable Characteristics**:
      - Write `fuzz_write.py` to send small payloads (e.g., `x01`, `x00x01`, etc.) to "write" or "write-without-response" characteristics.
      - Observe if measurement triggers or notifications change.

   7. **Analyze Data**:
      - Compare multiple readings from different colors to identify RGB/XYZ/LAB fields.
      - Parse and save the data to a CSV file.

   **Tools & Tips**:
   - Use the nRF Connect app to verify UUIDs and properties before Python testing.
   - For deeper sniffing, use Ubertooth One or nRF52840 with Wireshark.
   - Clear the GATT cache if needed (OS-specific steps).

   **Goal**:
   - Extract usable color data directly from the ColorReader for PC-side integration without the official mobile app.

2. **Coding Guidelines**:
   - Follow the existing code style and conventions.
   - Write clear and concise comments to explain complex logic.
   - Ensure that all new code is covered by unit tests.
   - Optimize for performance and memory usage where applicable.
   - Keep dependencies up to date and follow best practices for security.
   - Use a modular design approach to improve code maintainability and reusability.
   - Use `pygame` for the main UI.

3. **Naming Conventions**:
   - Use PascalCase for component names, interfaces, and type aliases.
   - Use camelCase for variables, functions, and methods.
   - Prefix private class members with an underscore (`_`).
   - Use ALL_CAPS for constants.

4. **Error Handling**:
   - Use `try/catch` blocks for async operations.
   - Implement proper error boundaries in components.
   - Always log errors with contextual information.
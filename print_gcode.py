#!/usr/bin/env python3
"""
G-code sender for Ender 3 V2
Sends G-code file to printer via serial connection
"""

import serial
import time
import sys
import os

def send_gcode(port='/dev/ttyUSB0', baudrate=115200, gcode_file='gcodes/print_test.gcode'):
    """Send G-code file to printer"""
    
    print(f"Opening {gcode_file}...")
    if not os.path.exists(gcode_file):
        print(f"Error: G-code file not found: {gcode_file}")
        return False
    
    with open(gcode_file, 'r') as f:
        lines = f.readlines()
    
    total_lines = len(lines)
    print(f"G-code file has {total_lines} lines")
    
    print(f"\nConnecting to printer on {port} at {baudrate} baud...")
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=5,
            write_timeout=5,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False
        )
        
        # Reset the connection
        ser.setDTR(False)
        time.sleep(0.5)
        ser.setDTR(True)
        time.sleep(2)
        
        # Clear any startup messages
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Wait for printer to be ready
        print("Waiting for printer to be ready...")
        time.sleep(2)
        
        # Read any startup messages
        while ser.in_waiting:
            try:
                msg = ser.readline().decode('utf-8', errors='ignore').strip()
                if msg:
                    print(f"  Printer: {msg}")
            except:
                pass
        
        # Test connection with M115
        print("\nTesting connection...")
        ser.write(b'M115\n')
        time.sleep(1)
        
        printer_ok = False
        for _ in range(10):
            if ser.in_waiting:
                try:
                    response = ser.readline().decode('utf-8', errors='ignore').strip()
                    if response:
                        print(f"  {response}")
                        if 'Ender' in response or 'Marlin' in response or 'ok' in response.lower():
                            printer_ok = True
                except:
                    pass
            time.sleep(0.2)
        
        if not printer_ok:
            print("Warning: Could not confirm printer connection, but will try anyway...")
        
        print("\n" + "="*50)
        print("Starting print!")
        print("="*50 + "\n")
        
        sent_lines = 0
        start_time = time.time()
        last_progress = 0
        
        for i, line in enumerate(lines):
            # Remove comments and whitespace
            original_line = line
            line = line.split(';')[0].strip()
            
            if not line:  # Skip empty lines and comment-only lines
                continue
            
            # Send the G-code command
            try:
                ser.write((line + '\n').encode())
            except Exception as e:
                print(f"\nWrite error: {e}")
                print("Attempting to reconnect...")
                ser.close()
                time.sleep(2)
                ser = serial.Serial(port, baudrate, timeout=5)
                time.sleep(2)
                ser.write((line + '\n').encode())
            
            # Wait for 'ok' response from printer
            timeout_count = 0
            while timeout_count < 60:  # 60 second timeout per command
                try:
                    if ser.in_waiting:
                        response = ser.readline().decode('utf-8', errors='ignore').strip()
                        if response:
                            if 'ok' in response.lower():
                                break
                            elif 'error' in response.lower():
                                print(f"\nPrinter error: {response}")
                                break
                            elif response.startswith('echo:'):
                                print(f"\n  {response}")
                    else:
                        time.sleep(0.1)
                        timeout_count += 0.1
                except Exception as e:
                    print(f"\nRead error: {e}")
                    time.sleep(0.5)
                    timeout_count += 0.5
            
            if timeout_count >= 60:
                print(f"\nTimeout waiting for response to: {line}")
            
            sent_lines += 1
            
            # Progress update every 1%
            progress = int((i + 1) / total_lines * 100)
            if progress > last_progress:
                last_progress = progress
                elapsed = time.time() - start_time
                print(f"\rProgress: {progress}% ({sent_lines} commands, {elapsed:.0f}s)", end='', flush=True)
        
        print(f"\n\n" + "="*50)
        print(f"Print complete!")
        print(f"Sent {sent_lines} commands in {time.time() - start_time:.0f} seconds")
        print("="*50)
        ser.close()
        return True
        
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        print("\nTroubleshooting tips:")
        print("1. Unplug and replug the USB cable")
        print("2. Make sure no other program is using the printer")
        print("3. Try: sudo chmod 666 /dev/ttyUSB0")
        return False
    except KeyboardInterrupt:
        print("\n\nPrint cancelled by user!")
        if 'ser' in locals() and ser.is_open:
            print("Sending stop commands...")
            try:
                ser.write(b'M104 S0\n')  # Turn off hotend
                ser.write(b'M140 S0\n')  # Turn off bed
                ser.write(b'M84\n')      # Disable motors
            except:
                pass
            ser.close()
        return False

if __name__ == '__main__':
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    gcode_path = os.path.join(script_dir, 'gcodes', 'print_test.gcode')
    
    print("=" * 50)
    print("Ender 3 V2 G-code Sender")
    print("=" * 50)
    print(f"\nG-code file: {gcode_path}")
    print("\nWARNING: Make sure the printer bed is clear and ready!")
    print("The print will start immediately after connecting.\n")
    
    response = input("Start print? (yes/no): ").strip().lower()
    if response in ['yes', 'y']:
        send_gcode(gcode_file=gcode_path)
    else:
        print("Print cancelled.")

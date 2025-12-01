"""
Ender 3 V2 Printer Controller
Handles serial communication with robust error handling and reconnection
"""

import serial
import serial.tools.list_ports
import time
import threading
import queue
from typing import Optional, Callable

class PrinterController:
    def __init__(self, baudrate=115200):
        self.baudrate = baudrate
        self.serial: Optional[serial.Serial] = None
        self.port = None
        self.connected = False
        self.printing = False
        self.paused = False
        self.progress = 0
        self.current_line = 0
        self.total_lines = 0
        self.gcode_lines = []
        self.print_thread: Optional[threading.Thread] = None
        self.stop_flag = False
        self.status_callback: Optional[Callable] = None
        self.temperature = {"bed": 0, "bed_target": 0, "hotend": 0, "hotend_target": 0}
        self.last_error = ""
        self.command_queue = queue.Queue()
        
    def find_printer(self) -> Optional[str]:
        """Find Ender 3 V2 on available ports"""
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if "1a86:7523" in port.hwid.lower() or "ch340" in port.description.lower():
                return port.device
        # Also check for ttyUSB devices
        for port in ports:
            if "ttyUSB" in port.device:
                return port.device
        return None
    
    def connect(self, port: Optional[str] = None) -> bool:
        """Connect to the printer"""
        try:
            if port is None:
                port = self.find_printer()
            
            if port is None:
                self.last_error = "No printer found. Check USB connection."
                return False
            
            self.port = port
            
            # Close existing connection
            if self.serial and self.serial.is_open:
                self.serial.close()
                time.sleep(0.5)
            
            self.serial = serial.Serial(
                port=port,
                baudrate=self.baudrate,
                timeout=2,
                write_timeout=2,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            
            # Reset connection
            self.serial.setDTR(False)
            time.sleep(0.3)
            self.serial.setDTR(True)
            time.sleep(2)
            
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            
            # Test connection
            self.serial.write(b'M115\n')
            time.sleep(1)
            
            response = ""
            while self.serial.in_waiting:
                response += self.serial.readline().decode('utf-8', errors='ignore')
            
            if 'Marlin' in response or 'ok' in response.lower():
                self.connected = True
                self.last_error = ""
                # Start temperature monitoring
                self._start_temp_monitoring()
                return True
            else:
                self.last_error = "Printer not responding"
                return False
                
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            return False
    
    def disconnect(self):
        """Disconnect from printer"""
        self.stop_print()
        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
            except:
                pass
        self.connected = False
        self.serial = None
    
    def reconnect(self) -> bool:
        """Attempt to reconnect to printer"""
        self.disconnect()
        time.sleep(1)
        return self.connect(self.port)
    
    def send_command(self, command: str, wait_for_ok: bool = True, timeout: float = 30) -> tuple[bool, str]:
        """Send a G-code command with robust error handling"""
        if not self.connected or not self.serial:
            return False, "Not connected"
        
        try:
            command = command.strip()
            if not command:
                return True, ""
            
            self.serial.write((command + '\n').encode())
            
            if not wait_for_ok:
                return True, ""
            
            response = ""
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                try:
                    if self.serial.in_waiting:
                        line = self.serial.readline().decode('utf-8', errors='ignore').strip()
                        response += line + "\n"
                        
                        # Parse temperature
                        if line.startswith('T:') or ' T:' in line:
                            self._parse_temperature(line)
                        
                        if 'ok' in line.lower():
                            return True, response
                        elif 'error' in line.lower():
                            return False, response
                    else:
                        time.sleep(0.01)
                except OSError as e:
                    # USB disconnected - try to reconnect
                    self.last_error = f"USB Error: {e}"
                    if self.reconnect():
                        return self.send_command(command, wait_for_ok, timeout)
                    return False, str(e)
            
            return False, "Timeout"
            
        except Exception as e:
            self.last_error = str(e)
            return False, str(e)
    
    def _parse_temperature(self, line: str):
        """Parse temperature from response"""
        try:
            # Parse format: T:200.00 /200.00 B:60.00 /60.00
            import re
            
            # Match hotend temp: T:xxx.xx or T:xxx.xx /xxx.xx
            hotend_match = re.search(r'T:([\d.]+)\s*/\s*([\d.]+)', line)
            if hotend_match:
                self.temperature["hotend"] = float(hotend_match.group(1))
                self.temperature["hotend_target"] = float(hotend_match.group(2))
            else:
                hotend_match = re.search(r'T:([\d.]+)', line)
                if hotend_match:
                    self.temperature["hotend"] = float(hotend_match.group(1))
            
            # Match bed temp: B:xxx.xx or B:xxx.xx /xxx.xx
            bed_match = re.search(r'B:([\d.]+)\s*/\s*([\d.]+)', line)
            if bed_match:
                self.temperature["bed"] = float(bed_match.group(1))
                self.temperature["bed_target"] = float(bed_match.group(2))
            else:
                bed_match = re.search(r'B:([\d.]+)', line)
                if bed_match:
                    self.temperature["bed"] = float(bed_match.group(1))
        except Exception as e:
            pass
    
    def _start_temp_monitoring(self):
        """Start background temperature monitoring"""
        def monitor():
            while self.connected:
                try:
                    if not self.printing:
                        self.send_command('M105', wait_for_ok=True, timeout=5)
                    time.sleep(2)
                except:
                    pass
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
    
    def load_gcode(self, filepath: str) -> bool:
        """Load G-code file"""
        try:
            with open(filepath, 'r') as f:
                self.gcode_lines = f.readlines()
            self.total_lines = len(self.gcode_lines)
            self.current_line = 0
            self.progress = 0
            return True
        except Exception as e:
            self.last_error = str(e)
            return False
    
    def load_gcode_content(self, content: str) -> bool:
        """Load G-code from string content"""
        try:
            self.gcode_lines = content.split('\n')
            self.total_lines = len(self.gcode_lines)
            self.current_line = 0
            self.progress = 0
            return True
        except Exception as e:
            self.last_error = str(e)
            return False
    
    def start_print(self) -> bool:
        """Start printing loaded G-code"""
        if not self.connected:
            self.last_error = "Not connected to printer"
            return False
        
        if not self.gcode_lines:
            self.last_error = "No G-code loaded"
            return False
        
        if self.printing:
            self.last_error = "Already printing"
            return False
        
        self.stop_flag = False
        self.paused = False
        self.printing = True
        self.current_line = 0
        
        self.print_thread = threading.Thread(target=self._print_loop, daemon=True)
        self.print_thread.start()
        
        return True
    
    def _print_loop(self):
        """Main print loop"""
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        
        while self.current_line < self.total_lines and not self.stop_flag:
            # Check stop flag frequently
            if self.stop_flag:
                break
                
            if self.paused:
                time.sleep(0.1)
                continue
            
            line = self.gcode_lines[self.current_line]
            
            # Remove comments and whitespace
            command = line.split(';')[0].strip()
            
            if command:
                # Check stop flag before sending
                if self.stop_flag:
                    break
                success, response = self.send_command(command, timeout=30)  # Reduced timeout
                
                if not success:
                    if 'USB' in self.last_error or 'Error' in self.last_error:
                        reconnect_attempts += 1
                        if reconnect_attempts >= max_reconnect_attempts:
                            self.last_error = "Too many connection errors, stopping print"
                            break
                        time.sleep(2)
                        if self.reconnect():
                            continue  # Retry the same line
                    else:
                        # Non-fatal error, continue
                        pass
                else:
                    reconnect_attempts = 0
            
            self.current_line += 1
            self.progress = int((self.current_line / self.total_lines) * 100)
            
            if self.status_callback:
                self.status_callback({
                    'progress': self.progress,
                    'current_line': self.current_line,
                    'total_lines': self.total_lines,
                    'temperature': self.temperature
                })
        
        self.printing = False
        if not self.stop_flag:
            self.progress = 100
    
    def pause_print(self):
        """Pause the current print"""
        if self.printing:
            self.paused = True
            # Retract and move up
            self.send_command('G91', wait_for_ok=False)  # Relative positioning
            self.send_command('G1 E-5 F300', wait_for_ok=False)  # Retract
            self.send_command('G1 Z10 F300', wait_for_ok=False)  # Move up
            self.send_command('G90', wait_for_ok=False)  # Absolute positioning
    
    def resume_print(self):
        """Resume paused print"""
        if self.printing and self.paused:
            self.send_command('G91', wait_for_ok=False)
            self.send_command('G1 Z-10 F300', wait_for_ok=False)  # Move back down
            self.send_command('G1 E5 F300', wait_for_ok=False)  # Prime
            self.send_command('G90', wait_for_ok=False)
            self.paused = False
    
    def stop_print(self):
        """Stop the current print - non-blocking"""
        self.stop_flag = True
        self.paused = False
        self.printing = False
        
        # Don't wait for thread, just let it die
        # Run cleanup in background thread to not block
        def cleanup():
            if self.connected and self.serial:
                try:
                    # Send emergency stop commands without waiting
                    self.serial.write(b'M104 S0\n')  # Hotend off
                    time.sleep(0.1)
                    self.serial.write(b'M140 S0\n')  # Bed off
                    time.sleep(0.1)
                    self.serial.write(b'M84\n')  # Disable motors
                except:
                    pass
        
        cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        cleanup_thread.start()
    
    def home(self):
        """Home all axes"""
        return self.send_command('G28')
    
    def get_status(self) -> dict:
        """Get current printer status"""
        return {
            'connected': self.connected,
            'printing': self.printing,
            'paused': self.paused,
            'progress': self.progress,
            'current_line': self.current_line,
            'total_lines': self.total_lines,
            'temperature': self.temperature,
            'port': self.port,
            'error': self.last_error
        }

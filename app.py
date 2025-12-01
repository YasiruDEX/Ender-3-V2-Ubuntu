"""
Ender 3 V2 Dashboard - Flask Backend
Apple-style web interface for 3D printer control
"""

from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
import cv2
import threading
import time
import os
import json
from werkzeug.utils import secure_filename
from printer_controller import PrinterController

app = Flask(__name__)
CORS(app)

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'gcode', 'g'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Global instances
printer = PrinterController()
camera = None
camera_lock = threading.Lock()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_camera():
    global camera
    with camera_lock:
        if camera is None:
            camera = cv2.VideoCapture(0)
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            camera.set(cv2.CAP_PROP_FPS, 30)
    return camera

def release_camera():
    global camera
    with camera_lock:
        if camera is not None:
            camera.release()
            camera = None

def generate_frames():
    """Generate video frames for streaming"""
    cam = get_camera()
    while True:
        with camera_lock:
            if cam is None or not cam.isOpened():
                cam = get_camera()
            success, frame = cam.read()
        
        if not success:
            # Send a placeholder frame
            frame = create_placeholder_frame()
        else:
            # Add timestamp overlay
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                       0.7, (255, 255, 255), 2)
            
            # Add printer status if connected
            if printer.connected:
                status_text = f"Bed: {printer.temperature['bed']:.0f}C | Hotend: {printer.temperature['hotend']:.0f}C"
                cv2.putText(frame, status_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                           0.6, (0, 255, 0), 2)
        
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        frame_bytes = buffer.tobytes()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        
        time.sleep(0.033)  # ~30 FPS

def create_placeholder_frame():
    """Create a placeholder frame when camera is not available"""
    import numpy as np
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (30, 30, 30)  # Dark gray
    cv2.putText(frame, "Camera not available", (400, 360), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (100, 100, 100), 2)
    return frame

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/status')
def get_status():
    return jsonify(printer.get_status())

@app.route('/api/connect', methods=['POST'])
def connect_printer():
    port = None
    if request.is_json and request.json:
        port = request.json.get('port')
    success = printer.connect(port)
    return jsonify({
        'success': success,
        'message': 'Connected successfully' if success else printer.last_error,
        'status': printer.get_status()
    })

@app.route('/api/disconnect', methods=['POST'])
def disconnect_printer():
    printer.disconnect()
    return jsonify({'success': True, 'message': 'Disconnected'})

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'})
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Load the G-code
        success = printer.load_gcode(filepath)
        
        return jsonify({
            'success': success,
            'message': f'Loaded {filename} ({printer.total_lines} lines)' if success else printer.last_error,
            'filename': filename,
            'lines': printer.total_lines
        })
    
    return jsonify({'success': False, 'message': 'Invalid file type'})

@app.route('/api/start', methods=['POST'])
def start_print():
    success = printer.start_print()
    return jsonify({
        'success': success,
        'message': 'Print started' if success else printer.last_error
    })

@app.route('/api/pause', methods=['POST'])
def pause_print():
    printer.pause_print()
    return jsonify({'success': True, 'message': 'Print paused'})

@app.route('/api/resume', methods=['POST'])
def resume_print():
    printer.resume_print()
    return jsonify({'success': True, 'message': 'Print resumed'})

@app.route('/api/stop', methods=['POST'])
def stop_print():
    printer.stop_print()
    return jsonify({'success': True, 'message': 'Print stopped'})

@app.route('/api/home', methods=['POST'])
def home_printer():
    if not printer.connected:
        return jsonify({'success': False, 'message': 'Printer not connected'})
    success, _ = printer.home()
    return jsonify({
        'success': success,
        'message': 'Homing complete' if success else 'Homing failed'
    })

@app.route('/api/command', methods=['POST'])
def send_command():
    if not printer.connected:
        return jsonify({'success': False, 'message': 'Printer not connected'})
    
    command = request.json.get('command', '')
    success, response = printer.send_command(command)
    return jsonify({
        'success': success,
        'response': response
    })

@app.route('/api/temperature', methods=['POST'])
def set_temperature():
    if not printer.connected:
        return jsonify({'success': False, 'message': 'Printer not connected'})
    
    bed = request.json.get('bed')
    hotend = request.json.get('hotend')
    
    if bed is not None:
        printer.send_command(f'M140 S{bed}', wait_for_ok=False)
    if hotend is not None:
        printer.send_command(f'M104 S{hotend}', wait_for_ok=False)
    
    return jsonify({'success': True})

@app.route('/api/ports')
def list_ports():
    import serial.tools.list_ports
    ports = []
    for port in serial.tools.list_ports.comports():
        ports.append({
            'device': port.device,
            'description': port.description,
            'hwid': port.hwid
        })
    return jsonify(ports)

if __name__ == '__main__':
    print("\n" + "="*50)
    print("Ender 3 V2 Dashboard")
    print("="*50)
    print("\nStarting server on http://localhost:3034")
    print("Press Ctrl+C to stop\n")
    
    app.run(host='0.0.0.0', port=3034, debug=False, threaded=True)

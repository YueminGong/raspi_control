#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import socket
import threading
import subprocess
import os
import time
import serial
import wave
import pyaudio
from tqdm import tqdm
from picamera2 import Picamera2
from datetime import datetime
import sys
import struct
import argparse
import logging

# ==================== 全局配置 ====================
UDP_IP = "0.0.0.0"  # 监听所有网络接口
UDP_PORT = 8888
BUFFER_SIZE = 1024
FILE_RECEIVE_PORT = 8889
FILE_SAVE_PATH = "/home/ppt5517/received_files/"
SERIAL_PORTS = ["/dev/ttyACM0", "/dev/ttyACM1"]
LED_GPIO_PIN = 26
AUDIO_RECORD_PATH = os.path.join(FILE_SAVE_PATH, "audio_recordings")
TCP_FILE_PORT = 8890        # 本地TCP文件接收端口
TCP_TARGET_PORT = 8890      # 电脑端TCP接收端口

# HDMI配置
HDMI_DISPLAY = "2"  # HDMI1接口 (HDMI0=2)

# 指令映射配置 (16进制编码)
COMMAND_MAPPING = {
    # 摄像头和音频控制指令 (0x14BD开头)
    'capture': '0x14BD0001',
    'continuous_capture': '0x14BD0002',
    'continuous_capture stop': '0x14BD0003',
    'record': '0x14BD0004',
    'audio_record_start': '0x14BD0005',
    'audio_record_stop': '0x14BD0006',
    'audio_play': '0x14BD0007',
    
    # 系统时间指令 (0x42EB开头)
    'set_time': '0x42EB0001',
    
    # 文件传输指令
    'file_transfer': '0x20010001',
    'download': '0x20010002',
    
    # HDMI控制指令
    'hdmi_play': '0x22A10001',
    'hdmi_stop': '0x22A10002',
    
    # TCP文件传输指令
    'tcp_send_file': '0x30C10001',
    
    # 其他指令
    'run_script oled': '0x30020001',
    'led_on': '0x40010001',
    'led_off': '0x40020001',
    'display': '0x50010001',
    'serial': '0x60010001'
}

SCRIPT_PATHS = {
    'oled': '/home/ppt5517/OLED_Module_Code/RaspberryPi/python/example/ppt.py'
}

# 音频参数
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE = 16000
AUDIO_CHUNK = 2048

# ==================== 初始化部分 ====================
# 创建必要的目录
os.makedirs(FILE_SAVE_PATH, exist_ok=True)
os.makedirs(AUDIO_RECORD_PATH, exist_ok=True)

# 摄像头初始化
picam2 = Picamera2()
camera_config = picam2.create_video_configuration(main={"size": (1920, 1080)})
picam2.configure(camera_config)
is_video_recording = False
continuous_capture = False
capture_interval = 1.0

# HDMI播放进程
hdmi_player_process = None

# GPIO初始化
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(LED_GPIO_PIN, GPIO.OUT)
    GPIO.output(LED_GPIO_PIN, GPIO.LOW)
    GPIO_AVAILABLE = True
except ImportError:
    print("警告: GPIO库不可用,LED控制功能将禁用")

# 串口初始化
ser_connections = {}
for port in SERIAL_PORTS:
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        ser_connections[port] = ser
        print(f"串口 {port} 初始化成功")
    except serial.SerialException:
        ser_connections[port] = None
        print(f"警告: 无法打开串口 {port}")

# 音频初始化
audio = pyaudio.PyAudio()
is_audio_recording = False
audio_stream = None
audio_frames = []

# 日志配置
logging.basicConfig(
    filename=os.path.join(FILE_SAVE_PATH, 'system.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ==================== HDMI功能 ====================
def hdmi_play_video(filepath):
    """通过HDMI播放视频"""
    global hdmi_player_process
    
    if not os.path.exists(filepath):
        return False, "文件不存在"
    
    try:
        # 停止当前播放
        if hdmi_player_process:
            hdmi_player_process.terminate()
            time.sleep(1)
        
        # 使用omxplayer播放
        cmd = [
            "omxplayer",
            f"--display={HDMI_DISPLAY}",
            "--no-osd",
            "--aspect-mode=fill",
            filepath
        ]
        hdmi_player_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True, f"正在HDMI播放: {filepath}"
    except Exception as e:
        return False, str(e)

def hdmi_stop_video():
    """停止HDMI播放"""
    global hdmi_player_process
    try:
        if hdmi_player_process:
            hdmi_player_process.terminate()
            hdmi_player_process = None
            return True, "HDMI播放已停止"
        return False, "没有正在播放的视频"
    except Exception as e:
        return False, str(e)

# ==================== TCP文件传输功能 ====================
def tcp_send_file(target_ip, filepath, port=TCP_TARGET_PORT):
    """通过TCP发送文件到目标电脑"""
    if not os.path.exists(filepath):
        return False, "文件不存在"
    
    try:
        filesize = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(30)
            sock.connect((target_ip, port))
            
            # 发送文件头 (4字节文件大小 + 文件名)
            header = struct.pack('>I', filesize) + filename.encode()
            sock.sendall(header)
            
            # 发送文件内容
            with open(filepath, 'rb') as f:
                with tqdm(total=filesize, unit='B', unit_scale=True, 
                         desc=f"发送 {filename}") as pbar:
                    while True:
                        chunk = f.read(4096)
                        if not chunk:
                            break
                        sock.sendall(chunk)
                        pbar.update(len(chunk))
            
            # 等待确认
            response = sock.recv(1024)
            if response == b"SUCCESS":
                return True, f"文件已发送至 {target_ip}:{port}"
            else:
                return False, f"传输失败: {response.decode()}"
    except Exception as e:
        return False, str(e)

def handle_tcp_file_transfer(conn, addr):
    """处理TCP文件上传"""
    try:
        logging.info(f"新的文件上传连接: {addr}")
        
        # 接收文件头 (6字节: 2字节指令码 + 4字节文件大小)
        header = conn.recv(6)
        if len(header) != 6:
            raise ValueError("无效的文件头长度")
            
        cmd_code = header[:2]
        file_size = struct.unpack('>I', header[2:])[0]
        
        logging.info(f"文件大小: {file_size} 字节")
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"upload_{timestamp}.bin"
        filepath = os.path.join(FILE_SAVE_PATH, filename)
        
        # 接收文件数据
        received = 0
        with open(filepath, 'wb') as f:
            while received < file_size:
                chunk = conn.recv(min(4096, file_size - received))
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
        
        if received == file_size:
            logging.info(f"文件接收完成: {filepath}")
            conn.sendall(b"FILE_TRANSFER_SUCCESS")
            
            # 尝试根据内容识别文件类型
            new_path = guess_file_type(filepath)
            return new_path
        else:
            os.remove(filepath)
            raise ValueError(f"文件接收不完整 ({received}/{file_size} 字节)")
            
    except Exception as e:
        logging.error(f"文件传输错误: {str(e)}")
        try:
            conn.sendall(f"ERROR:{str(e)}".encode())
        except:
            pass
        return None

# ==================== 其他核心功能函数 ====================
def get_command_code(full_command):
    """获取命令对应的16进制指令码"""
    base_cmd = full_command.split()[0] if ' ' in full_command else full_command
    if full_command == 'continuous_capture stop':
        base_cmd = full_command
    return COMMAND_MAPPING.get(base_cmd, '0x00000000')

def capture_image():
    """拍摄单张照片"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(FILE_SAVE_PATH, f"capture_{timestamp}.jpg")
    
    try:
        picam2.start()
        time.sleep(1)  # 等待摄像头稳定
        picam2.capture_file(filename)
        picam2.stop()
        return True, filename
    except Exception as e:
        return False, str(e)

def start_continuous_capture(interval=1.0):
    """开始连续拍摄"""
    global continuous_capture, capture_interval
    
    if continuous_capture:
        return False, "已经在连续拍摄模式"
    
    capture_interval = interval
    continuous_capture = True
    
    def capture_loop():
        picam2.start()
        while continuous_capture:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(FILE_SAVE_PATH, f"continuous_{timestamp}.jpg")
            picam2.capture_file(filename)
            time.sleep(capture_interval)
        picam2.stop()
    
    threading.Thread(target=capture_loop, daemon=True).start()
    return True, f"开始连续拍摄，间隔 {interval} 秒"

def stop_continuous_capture():
    """停止连续拍摄"""
    global continuous_capture
    continuous_capture = False
    return True, "已停止连续拍摄"

def record_video(duration):
    """录制视频"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(FILE_SAVE_PATH, f"video_{timestamp}.mp4")
    
    try:
        video_config = picam2.create_video_configuration()
        picam2.configure(video_config)
        picam2.start()
        time.sleep(1)  # 等待摄像头稳定
        picam2.start_and_record_video(filename, duration=duration)
        picam2.stop()
        return True, filename
    except Exception as e:
        if picam2.started:
            picam2.stop()
        return False, str(e)

def set_system_time(time_str):
    """设置系统时间"""
    try:
        # 验证时间格式
        datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        
        # 设置时间 (需要sudo权限)
        result = subprocess.run(
            ['sudo', 'timedatectl', 'set-time', time_str],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True, f"系统时间已设置为 {time_str}"
        else:
            return False, result.stderr
    except ValueError:
        return False, "时间格式错误，应为 YYYY-MM-DD HH:MM:SS"
    except Exception as e:
        return False, str(e)

def control_led(state):
    """控制LED灯带"""
    if not GPIO_AVAILABLE:
        return False, "GPIO不可用"
    
    try:
        if state.lower() == 'on':
            GPIO.output(LED_GPIO_PIN, GPIO.HIGH)
            return True, "LED已开启"
        elif state.lower() == 'off':
            GPIO.output(LED_GPIO_PIN, GPIO.LOW)
            return True, "LED已关闭"
        else:
            return False, "无效的状态 (on/off)"
    except Exception as e:
        return False, str(e)

def display_file(filepath):
    """显示图片或视频"""
    if not os.path.exists(filepath):
        return False, "文件不存在"
    
    try:
        if filepath.lower().endswith(('.png', '.jpg', '.jpeg')):
            # 显示图片
            subprocess.Popen(["fbi", "-T", "1", "-noverbose", "-a", filepath])
            return True, f"正在显示图片: {filepath}"
        elif filepath.lower().endswith(('.mp4', '.avi', '.h264')):
            # 播放视频
            subprocess.Popen(["omxplayer", filepath])
            return True, f"正在播放视频: {filepath}"
        else:
            return False, "不支持的文件格式"
    except Exception as e:
        return False, str(e)

def serial_send(port, data):
    """通过串口发送数据"""
    if port not in ser_connections or ser_connections[port] is None:
        return False, f"串口 {port} 不可用"
    
    try:
        ser_connections[port].write(data.encode())
        return True, f"已通过 {port} 发送: {data}"
    except Exception as e:
        return False, str(e)

def start_audio_recording():
    """开始录音"""
    global is_audio_recording, audio_stream, audio_frames
    
    if is_audio_recording:
        return False, "已经在录音中"
    
    try:
        audio_frames = []
        audio_stream = audio.open(
            format=AUDIO_FORMAT,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            frames_per_buffer=AUDIO_CHUNK
        )
        is_audio_recording = True
        
        def recording_thread():
            while is_audio_recording:
                data = audio_stream.read(AUDIO_CHUNK)
                audio_frames.append(data)
        
        threading.Thread(target=recording_thread, daemon=True).start()
        return True, "录音已开始"
    except Exception as e:
        return False, str(e)

def stop_audio_recording():
    """停止录音并保存"""
    global is_audio_recording, audio_stream, audio_frames
    
    if not is_audio_recording:
        return False, "没有正在进行的录音"
    
    try:
        is_audio_recording = False
        audio_stream.stop_stream()
        audio_stream.close()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(AUDIO_RECORD_PATH, f"recording_{timestamp}.wav")
        
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(AUDIO_CHANNELS)
            wf.setsampwidth(audio.get_sample_size(AUDIO_FORMAT))
            wf.setframerate(AUDIO_RATE)
            wf.writeframes(b''.join(audio_frames))
        
        audio_frames = []
        return True, f"录音已保存: {filename}"
    except Exception as e:
        return False, str(e)

def play_audio(filepath):
    """播放音频文件"""
    if not os.path.exists(filepath):
        return False, "文件不存在"
    
    try:
        if not filepath.lower().endswith('.wav'):
            return False, "仅支持WAV格式音频"
        
        def play_thread():
            with wave.open(filepath, 'rb') as wf:
                stream = audio.open(
                    format=audio.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True
                )
                
                data = wf.readframes(AUDIO_CHUNK)
                while data:
                    stream.write(data)
                    data = wf.readframes(AUDIO_CHUNK)
                
                stream.stop_stream()
                stream.close()
        
        threading.Thread(target=play_thread, daemon=True).start()
        return True, f"正在播放: {filepath}"
    except Exception as e:
        return False, str(e)

def run_oled_script():
    """运行OLED显示脚本"""
    try:
        script_path = SCRIPT_PATHS['oled']
        result = subprocess.run(
            ['/usr/bin/python3', script_path],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True, "OLED脚本执行成功"
        else:
            return False, result.stderr
    except Exception as e:
        return False, str(e)

def guess_file_type(filepath):
    """根据文件内容猜测文件类型并重命名"""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(4)
            
        new_path = filepath
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if header.startswith(b'\xFF\xD8\xFF'):  # JPEG
            new_path = os.path.join(FILE_SAVE_PATH, f"image_{timestamp}.jpg")
        elif header.startswith(b'\x89PNG'):  # PNG
            new_path = os.path.join(FILE_SAVE_PATH, f"image_{timestamp}.png")
        elif header.startswith(b'\x00\x00\x00\x20'):  # MP4
            new_path = os.path.join(FILE_SAVE_PATH, f"video_{timestamp}.mp4")
        elif header.startswith(b'RIFF') and header[8:12] == b'WAVE':  # WAV
            new_path = os.path.join(AUDIO_RECORD_PATH, f"audio_{timestamp}.wav")
            
        if new_path != filepath:
            os.rename(filepath, new_path)
            logging.info(f"文件已重命名为: {new_path}")
            
        return new_path
    except Exception as e:
        logging.error(f"文件类型检测失败: {str(e)}")
        return filepath

# ==================== 网络服务线程 ====================
def udp_command_server():
    """UDP命令服务器"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    logging.info(f"UDP命令服务器已启动，监听 {UDP_IP}:{UDP_PORT}")
    
    while True:
        data, addr = sock.recvfrom(BUFFER_SIZE)
        command = data.decode().strip()
        cmd_code = get_command_code(command)
        logging.info(f"收到命令 [{cmd_code}]: {command}")
        
        try:
            # 解析命令参数
            parts = command.split()
            base_cmd = parts[0] if len(parts) > 0 else ""
            
            # 根据指令码处理命令
            if cmd_code == COMMAND_MAPPING['capture']:
                success, result = capture_image()
            elif cmd_code == COMMAND_MAPPING['continuous_capture']:
                if len(parts) > 1:
                    try:
                        interval = float(parts[1])
                        success, result = start_continuous_capture(interval)
                    except ValueError:
                        success, result = False, "无效的间隔时间"
                else:
                    success, result = start_continuous_capture()
            elif cmd_code == COMMAND_MAPPING['continuous_capture stop']:
                success, result = stop_continuous_capture()
            elif cmd_code == COMMAND_MAPPING['record']:
                if len(parts) > 1:
                    try:
                        duration = float(parts[1])
                        success, result = record_video(duration)
                    except ValueError:
                        success, result = False, "无效的录制时长"
                else:
                    success, result = False, "需要指定录制时长"
            elif cmd_code == COMMAND_MAPPING['set_time']:
                if len(parts) > 1:
                    success, result = set_system_time(' '.join(parts[1:]))
                else:
                    success, result = False, "需要指定时间"
            elif cmd_code == COMMAND_MAPPING['led_on']:
                success, result = control_led('on')
            elif cmd_code == COMMAND_MAPPING['led_off']:
                success, result = control_led('off')
            elif cmd_code == COMMAND_MAPPING['display']:
                if len(parts) > 1:
                    success, result = display_file(parts[1])
                else:
                    success, result = False, "需要指定文件路径"
            elif cmd_code == COMMAND_MAPPING['serial']:
                if len(parts) > 2:
                    success, result = serial_send(parts[1], ' '.join(parts[2:]))
                else:
                    success, result = False, "需要指定串口和数据"
            elif cmd_code == COMMAND_MAPPING['audio_record_start']:
                success, result = start_audio_recording()
            elif cmd_code == COMMAND_MAPPING['audio_record_stop']:
                success, result = stop_audio_recording()
            elif cmd_code == COMMAND_MAPPING['audio_play']:
                if len(parts) > 1:
                    success, result = play_audio(parts[1])
                else:
                    success, result = False, "需要指定音频文件"
            elif cmd_code == COMMAND_MAPPING['run_script oled']:
                success, result = run_oled_script()
            elif cmd_code == COMMAND_MAPPING['hdmi_play']:
                if len(parts) > 1:
                    success, result = hdmi_play_video(parts[1])
                else:
                    success, result = False, "需要指定视频文件"
            elif cmd_code == COMMAND_MAPPING['hdmi_stop']:
                success, result = hdmi_stop_video()
            elif cmd_code == COMMAND_MAPPING['tcp_send_file']:
                if len(parts) > 2:
                    success, result = tcp_send_file(parts[1], parts[2])
                else:
                    success, result = False, "需要指定目标IP和文件路径"
            else:
                success, result = False, "未知命令"
            
            # 构建响应
            status = "SUCCESS" if success else "ERROR"
            response = f"{cmd_code}:{status}:{result}"
            
        except Exception as e:
            response = f"{cmd_code}:ERROR:{str(e)}"
        
        sock.sendto(response.encode(), addr)
        logging.info(f"发送响应: {response}")

def tcp_file_server():
    """TCP文件传输服务器"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((UDP_IP, TCP_FILE_PORT))
        s.listen(5)
        logging.info(f"TCP文件服务器已启动，监听 {UDP_IP}:{TCP_FILE_PORT}")
        
        while True:
            conn, addr = s.accept()
            logging.info(f"新的TCP连接: {addr}")
            threading.Thread(target=handle_tcp_file_transfer, args=(conn, addr)).start()

# ==================== 主程序 ====================
def cleanup():
    """清理资源"""
    global continuous_capture, is_audio_recording, hdmi_player_process
    
    # 停止连续拍摄
    continuous_capture = False
    
    # 停止录音
    if is_audio_recording:
        is_audio_recording = False
        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()
    
    # 停止HDMI播放
    if hdmi_player_process:
        hdmi_player_process.terminate()
    
    # 关闭摄像头
    if 'picam2' in globals():
        picam2.stop()
        picam2.close()
    
    # 清理GPIO
    if GPIO_AVAILABLE:
        GPIO.cleanup()
    
    # 关闭串口
    for port, ser in ser_connections.items():
        if ser:
            ser.close()
    
    # 关闭音频接口
    if 'audio' in globals():
        audio.terminate()
    
    logging.info("系统资源已清理")

def main():
    parser = argparse.ArgumentParser(description="多功能控制服务器")
    parser.add_argument('--udp_ip', default="0.0.0.0", help="UDP监听IP地址")
    parser.add_argument('--udp_port', type=int, default=8888, help="UDP监听端口")
    args = parser.parse_args()

    global UDP_IP, UDP_PORT
    UDP_IP = args.udp_ip
    UDP_PORT = args.udp_port

    print("=== 多功能控制服务器 ===")
    print(f"UDP命令端口: {UDP_IP}:{UDP_PORT}")
    print(f"TCP文件端口: {UDP_IP}:{TCP_FILE_PORT}")
    print(f"文件保存路径: {FILE_SAVE_PATH}")
    print("\n可用指令:")
    for cmd, code in sorted(COMMAND_MAPPING.items(), key=lambda x: x[1]):
        print(f"  {code}: {cmd}")

    try:
        # 启动UDP命令服务器
        udp_thread = threading.Thread(target=udp_command_server, daemon=True)
        udp_thread.start()
        
        # 启动TCP文件服务器
        tcp_thread = threading.Thread(target=tcp_file_server, daemon=True)
        tcp_thread.start()
        
        # 主循环
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n接收到中断信号，正在关闭...")
    finally:
        cleanup()
        print("服务器已关闭")

if __name__ == "__main__":
    main()

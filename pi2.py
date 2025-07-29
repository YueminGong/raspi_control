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

# 全局配置
UDP_IP = "0.0.0.0"  # 监听所有接口
UDP_PORT = 8888
BUFFER_SIZE = 1024
FILE_RECEIVE_PORT = 8889
FILE_SAVE_PATH = "/home/ppt5517/received_files/"
SERIAL_PORTS = ["/dev/ttyACM0", "/dev/ttyACM1"]
LED_GPIO_PIN = 26  # 使用GPIO26控制灯带
AUDIO_RECORD_PATH = os.path.join(FILE_SAVE_PATH, "audio_recordings")
FILE_SEND_IP = "192.168.1.7"  # 目标IP地址
FILE_SEND_PORT = 8800          # 目标端口

# 创建必要的目录
os.makedirs(FILE_SAVE_PATH, exist_ok=True)
os.makedirs(AUDIO_RECORD_PATH, exist_ok=True)

# 音频参数
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE = 16000
AUDIO_CHUNK = 2048

# 初始化摄像头
picam2 = Picamera2()
camera_config = picam2.create_video_configuration(main={"size": (1920, 1080)},
                                               encode="main")
picam2.configure(camera_config)
is_video_recording = False
continuous_capture = False
capture_interval = 1.0  # 连续拍摄间隔，默认1秒

# 音频状态
is_audio_recording = False

# 初始化GPIO
try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(LED_GPIO_PIN, GPIO.OUT)
    GPIO.output(LED_GPIO_PIN, GPIO.LOW)
    GPIO_AVAILABLE = True
except:
    print("GPIO模块不可用，灯带控制将无法工作")
    GPIO_AVAILABLE = False

# 初始化串口
ser_connections = {}
for port in SERIAL_PORTS:
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        ser_connections[port] = ser
        print(f"成功连接到串口 {port}")
    except:
        print(f"无法连接到串口 {port}")
        ser_connections[port] = None

# 指令定义
COMMANDS = {
    # 主指令
    0x0A5F: "display",        # 显示控制
    0x14BD: "control",        # 控制相关
    0x0011: "capture",        # 采集相关控制
    0x0022: "storage_clear",  # 相机及录音存储清除
    0x2E9A: "data_return",    # 回传控制
    0x42EB: "time_sync",      # 校时指令
    
    # 子指令
    "display": {
        0x0001: "display_image",  # 显示图片
        0x0002: "play_video"      # 播放视频
    },
    "control": {
        0x0001: "led_on",         # 打开灯带
        0x0002: "led_off",        # 关闭灯带
        0x0003: "serial_send"     # 串口发送
    },
    "capture": {
        0x0001: "single_capture",   # 单次拍照
        0x0002: "start_continuous", # 开始连续拍摄
        0x0003: "stop_continuous",  # 停止连续拍摄
        0x0004: "start_recording",  # 开始录像
        0x0005: "stop_recording"    # 停止录像
    },
    "data_return": {
        0x0001: "send_file",      # 发送文件
        0x0002: "send_status"      # 发送状态
    }
}

def send_file(filepath):
    """发送文件到指定服务器"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # 读取文件内容
        with open(filepath, 'rb') as f:
            file_data = f.read()
        
        # 获取文件名并编码为100字节
        filename = os.path.basename(filepath)
        filename_encoded = filename.encode('utf-8').ljust(100, b'\x00')[:100]
        
        # 组合文件名和数据
        data_to_send = filename_encoded + file_data
        
        # 发送文件
        sock.sendto(data_to_send, (FILE_SEND_IP, FILE_SEND_PORT))
        print(f"文件 {filename} 已发送到 {FILE_SEND_IP}:{FILE_SEND_PORT}")
        
    except Exception as e:
        print(f"发送文件时出错: {str(e)}")
    finally:
        if 'sock' in locals():
            sock.close()

def continuous_capture_func():
    """连续拍摄函数"""
    global continuous_capture
    picam2.start()
    
    while continuous_capture:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{FILE_SAVE_PATH}continuous_{timestamp}.jpg"
        picam2.capture_file(filename)
        time.sleep(capture_interval)
    
    picam2.stop()

def handle_udp_command():
    global is_video_recording, continuous_capture, is_audio_recording
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    
    print(f"UDP服务启动，监听端口 {UDP_PORT}")
    print("支持的指令:")
    for cmd_hex, cmd_name in COMMANDS.items():
        if isinstance(cmd_hex, int):
            print(f"0x{cmd_hex:04X} -> {cmd_name}")
    
    while True:
        data, addr = sock.recvfrom(BUFFER_SIZE)
        response = "ERROR:无效指令"
        
        try:
            # 解析指令 (前2字节主指令，后2字节子指令)
            if len(data) >= 4:
                command = int.from_bytes(data[:2], byteorder='big')
                sub_command = int.from_bytes(data[2:4], byteorder='big') if len(data) >= 4 else 0
                payload = data[4:] if len(data) > 4 else b''
                
                print(f"收到指令: 0x{command:04X}, 子指令: 0x{sub_command:04X}, 数据长度: {len(payload)}")
                
                # 查找主指令
                if command in COMMANDS:
                    cmd_name = COMMANDS[command]
                    
                    if cmd_name == "time_sync":  # 校时指令 (0x42EB)
                        try:
                            time_str = payload.decode('utf-8').strip()
                            datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                            return_code = os.system(f'sudo timedatectl set-time "{time_str}"')
                            if return_code == 0:
                                response = f"TIME_SET_SUCCESS:{time_str}"
                            else:
                                response = f"TIME_SET_FAILED:{return_code}"
                        except Exception as e:
                            response = f"ERROR:时间格式无效 - {str(e)}"
                    
                    elif cmd_name == "storage_clear":  # 存储清除 (0x22)
                        try:
                            # 清除相机和录音存储
                            for f in os.listdir(FILE_SAVE_PATH):
                                if f.startswith(('capture_', 'continuous_', 'video_', 'recording')):
                                    os.remove(os.path.join(FILE_SAVE_PATH, f))
                            response = "STORAGE_CLEARED"
                        except Exception as e:
                            response = f"ERROR:清除存储失败 - {str(e)}"
                    
                    elif cmd_name in COMMANDS and isinstance(COMMANDS[cmd_name], dict):
                        sub_cmds = COMMANDS[cmd_name]
                        
                        if sub_command in sub_cmds:
                            sub_cmd_name = sub_cmds[sub_command]
                            
                            if sub_cmd_name == "led_on":
                                if GPIO_AVAILABLE:
                                    GPIO.output(LED_GPIO_PIN, GPIO.HIGH)
                                    response = "LED_ON"
                                else:
                                    response = "ERROR:GPIO不可用"
                                    
                            elif sub_cmd_name == "led_off":
                                if GPIO_AVAILABLE:
                                    GPIO.output(LED_GPIO_PIN, GPIO.LOW)
                                    response = "LED_OFF"
                                else:
                                    response = "ERROR:GPIO不可用"
                            
                            elif sub_cmd_name == "single_capture":
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                filename = f"{FILE_SAVE_PATH}capture_{timestamp}.jpg"
                                picam2.start()
                                picam2.capture_file(filename)
                                picam2.stop()
                                response = f"CAPTURED:{filename}"
                            
                            elif sub_cmd_name == "start_continuous":
                                try:
                                    interval = float(payload.decode('utf-8')) if payload else 1.0
                                    if interval > 0:
                                        global capture_interval, continuous_capture
                                        capture_interval = interval
                                        continuous_capture = True
                                        threading.Thread(target=continuous_capture_func).start()
                                        response = f"CONTINUOUS_CAPTURE_STARTED:{interval}s"
                                    else:
                                        response = "ERROR:间隔时间必须大于0"
                                except:
                                    response = "ERROR:无效的间隔时间"
                            
                            elif sub_cmd_name == "stop_continuous":
                                continuous_capture = False
                                response = "CONTINUOUS_CAPTURE_STOPPED"
                            
                            elif sub_cmd_name == "display_image":
                                filepath = payload.decode('utf-8')
                                if os.path.exists(filepath):
                                    subprocess.Popen(["fbi", "-T", "1", "-noverbose", "-a", filepath])
                                    response = "IMAGE_DISPLAY_STARTED"
                                else:
                                    response = "ERROR:图片文件不存在"
                            
                            elif sub_cmd_name == "send_file":
                                filepath = payload.decode('utf-8')
                                if os.path.exists(filepath):
                                    threading.Thread(target=send_file, args=(filepath,)).start()
                                    response = f"FILE_SEND_STARTED:{filepath}"
                                else:
                                    response = "ERROR:文件不存在"
                            
                            elif sub_cmd_name == "serial_send":
                                parts = payload.decode('utf-8').split(':', 1)
                                if len(parts) == 2:
                                    port, data = parts
                                    if port in ser_connections and ser_connections[port] is not None:
                                        try:
                                            ser_connections[port].write(data.encode())
                                            response = f"SERIAL_SENT:{port}:{data}"
                                        except Exception as e:
                                            response = f"ERROR:串口发送失败 - {str(e)}"
                                    else:
                                        response = f"ERROR:串口 {port} 不可用"
                                else:
                                    response = "ERROR:串口命令格式应为'port:data'"
                            
                            # 可以继续添加其他子指令处理...
                            
                        else:
                            response = f"ERROR:无效的子指令 0x{sub_command:04X} 对于 {cmd_name}"
                else:
                    response = f"ERROR:未知指令 0x{command:04X}"
            else:
                response = "ERROR:指令长度不足"
                
        except Exception as e:
            response = f"ERROR:处理指令时出错 - {str(e)}"
            
        sock.sendto(response.encode(), addr)

def file_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, FILE_RECEIVE_PORT))
    
    while True:
        data, addr = sock.recvfrom(65535)
        try:
            # 解析:前100字节为文件名(ASCII)
            filename = data[:100].split(b'\x00')[0].decode('utf-8', errors='ignore').strip()
            if not filename:
                raise ValueError("无效文件名")
                
            file_data = data[100:]
            save_path = os.path.join(FILE_SAVE_PATH, filename)
            
            # 保存文件
            with open(save_path, 'wb') as f:
                f.write(file_data)
                
            sock.sendto(b"FILE_SAVED", addr)
        except Exception as e:
            sock.sendto(f"ERROR:{str(e)}".encode(), addr)

def cleanup():
    """清理资源"""
    # 停止摄像头相关功能
    global is_video_recording, continuous_capture
    if is_video_recording:
        picam2.stop_recording()
    continuous_capture = False
    picam2.close()
    
    # 停止音频相关功能
    os.system('pkill -f arecord')  # 确保停止所有录音
    os.system('pkill -f aplay')    # 确保停止所有播放
    
    # 清理GPIO
    if GPIO_AVAILABLE:
        GPIO.cleanup()
    
    # 关闭串口
    for port, ser in ser_connections.items():
        if ser is not None:
            ser.close()

if __name__ == "__main__":
    try:
        # 启动UDP命令处理线程
        command_thread = threading.Thread(target=handle_udp_command)
        command_thread.daemon = True
        command_thread.start()
        
        # 启动文件接收线程
        file_thread = threading.Thread(target=file_receiver)
        file_thread.daemon = True
        file_thread.start()
        
        print("系统已启动，等待指令...")
        print("支持的主指令:")
        for cmd_hex, cmd_name in COMMANDS.items():
            if isinstance(cmd_hex, int):
                print(f"0x{cmd_hex:04X} -> {cmd_name}")
        
        # 主线程保持运行
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
    finally:
        cleanup()
        print("系统已关闭")

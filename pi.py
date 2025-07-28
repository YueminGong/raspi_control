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
UDP_IP = "192.168.2.8"  # 监听所有接口
UDP_PORT = 8888
BUFFER_SIZE = 1024
FILE_RECEIVE_PORT = 8889
FILE_SAVE_PATH = "/home/ppt5517/received_files/"
SERIAL_PORTS = ["/dev/ttyACM0", "/dev/ttyACM1"]
LED_GPIO_PIN = 26  # 使用GPIO26控制灯带
AUDIO_RECORD_PATH = os.path.join(FILE_SAVE_PATH, "audio_recordings")

SCRIPT_PATHS = {
    'oled': '/home/ppt5517/OLED_Module_Code/RaspberryPi/python/example/ppt.py'
}

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
                                                encode= "main")
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

def handle_udp_command():
    global is_video_recording, continuous_capture, is_audio_recording
    """处理UDP命令的主函数"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    
    print(f"UDP服务启动，监听端口 {UDP_PORT}")
    
    while True:
        data, addr = sock.recvfrom(BUFFER_SIZE)
        command = data.decode().strip()
        print(f"收到来自 {addr} 的命令: {command}")
        
        response = "OK"
        
        try:
            if command == "capture":
                # 单次拍照
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{FILE_SAVE_PATH}capture_{timestamp}.jpg"
                picam2.start()
                picam2.capture_file(filename)
                picam2.stop()
                response = f"CAPTURED:{filename}"
                
            elif command.startswith("continuous_capture"):
                # 连续拍摄控制
                parts = command.split()
                global continuous_capture, capture_interval
                
                if len(parts) == 1:
                    # 开始连续拍摄（默认间隔）
                    continuous_capture = True
                    threading.Thread(target=continuous_capture_func).start()
                elif len(parts) == 2:
                    # 开始连续拍摄（指定间隔）
                    if parts[1] =="stop":
                        #stop
                        continuous_capture = False
                        response = "连续拍摄停止"
                    else:
                        try:
                            interval = float(parts[1])
                            if interval > 0:
                                capture_interval = interval
                                continuous_capture = True
                                threading.Thread(target=continuous_capture_func).start()
                            else:
                                response = "ERROR:间隔时间必须大于0"
                        except ValueError:
                            response = "ERROR:无效的间隔时间"
                else:
                    response = "ERROR:无效的连续拍摄命令"
                    
            elif command.startswith("record"):
                parts = command.split()
                if len(parts) == 2 and parts[0] == "record":
                    try:
                        duration = float(parts[1])
                        if duration <= 0:
                            response = "ERROR:时长必须大于0"
                        else:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            filename = f"{FILE_SAVE_PATH}video_{timestamp}.mp4"
                            
                            # 配置摄像头(根据需要进行调整)
                            try:
                                video_config = picam2.create_video_configuration()
                                picam2.configure(video_config)
                                picam2.start()
                                time.sleep(1)  # 等待摄像头稳定
                                picam2.start_and_record_video(filename, duration=duration)
                                picam2.stop()
                                response = f"RECORDING_COMPLETE:{filename}"
                            except Exception as e:
                                response = f"RECORDING_ERROR:{str(e)}"
                                if picam2.started:
                                    picam2.stop()
                    except ValueError:
                        response = "ERROR:无效时长"
                else:
                    response = "ERROR:格式应为'record <时长>'"

            elif command.strip() == 'run_script oled':  # 运行OLED脚本
                try:
                    script_path = SCRIPT_PATHS['oled']
                    audio_file = os.path.join(AUDIO_RECORD_PATH, "recording.wav")
                    
                    # 使用os.system运行脚本
                    return_code = os.system(f'/usr/bin/python3 {script_path}')
                    
                    if return_code == 0:
                        response = "SUCCESS:脚本执行成功"
                    else:
                        response = f"ERROR:脚本执行失败，返回码{return_code}"
                    
                except Exception as e:
                    response = f"ERROR:{str(e)}"
        
            elif command == "led_on":
                # 打开灯带
                if GPIO_AVAILABLE:
                    GPIO.output(LED_GPIO_PIN, GPIO.HIGH)
                else:
                    response = "ERROR:GPIO不可用"
                    
            elif command == "led_off":
                # 关闭灯带
                if GPIO_AVAILABLE:
                    GPIO.output(LED_GPIO_PIN, GPIO.LOW)
                else:
                    response = "ERROR:GPIO不可用"
                    
            elif command.startswith("display"):
                # 显示图片或视频
                parts = command.split()
                if len(parts) == 2:
                    filepath = parts[1]
                    if os.path.exists(filepath):
                        if filepath.lower().endswith(('.png', '.jpg', '.jpeg')):
                            # 显示图片
                            subprocess.Popen(["fbi", "-T", "1", "-noverbose", "-a", filepath])
                        elif filepath.lower().endswith(('.mp4', '.avi', '.h264')):
                            # 播放视频
                            subprocess.Popen(["omxplayer", filepath])
                        else:
                            response = "ERROR:不支持的文件格式"
                    else:
                        response = "ERROR:文件不存在"
                else:
                    response = "ERROR:无效的显示命令"
                    
            elif command.startswith("serial"):
                # 串口通信
                parts = command.split(maxsplit=3)
                if len(parts) >= 4:
                    port = parts[1]
                    if port in ser_connections and ser_connections[port] is not None:
                        try:
                            data_to_send = parts[3].encode()
                            ser_connections[port].write(data_to_send)
                            response = f"SERIAL_SENT:{port}:{parts[3]}"
                        except Exception as e:
                            response = f"ERROR:串口发送失败 - {str(e)}"
                    else:
                        response = f"ERROR:串口 {port} 不可用"
                else:
                    response = "ERROR:无效的串口命令格式，应为 'serial [port] [data]'"
                    
            elif command == "audio_record_start":
                # 开始录音
                audio_file = os.path.join(AUDIO_RECORD_PATH, "recording.wav")
                os.system(f'arecord -d 10 -f cd -t wav {audio_file} &')  # 后台运行录音
                response = f"AUDIO_RECORDING_STARTED:{audio_file}"
                
            elif command == "audio_record_stop":
                # 停止录音
                os.system('pkill -f arecord')  # 停止所有arecord进程
                response = "AUDIO_RECORDING_STOPPED"
                
            elif command.startswith("audio_play"):
                # 播放音频
                parts = command.split()
                if len(parts) == 2:
                    audio_file = parts[1]
                    if os.path.exists(audio_file):
                        os.system(f'aplay {audio_file} &')  # 后台播放
                        response = f"AUDIO_PLAYING:{audio_file}"
                    else:
                        response = "ERROR:音频文件不存在"
                else:
                    response = "ERROR:无效的播放命令格式"
                    
            else:
                response = "ERROR:未知命令"
                
        except Exception as e:
            response = f"ERROR:处理命令时出错 - {str(e)}"
            
        sock.sendto(response.encode(), addr)

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

def file_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, FILE_RECEIVE_PORT))  # FILE_PORT = 8889
    
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
        
        print("系统已启动，等待命令...")
        print("可用命令:")
        print("  capture - 单次拍照")
        print("  continuous_capture [interval] - 开始连续拍摄(可选间隔时间)")
        print("  continuous_capture stop - 停止连续拍摄")
        print("  record_start - 开始录像")
        print("  record_stop - 停止录像")
        print("  led_on - 打开灯带")
        print("  led_off - 关闭灯带")
        print("  display <filepath> - 显示图片或视频")
        print("  serial <port> <data> - 通过串口发送数据")
        print("  audio_record_start - 开始录音")
        print("  audio_record_stop - 停止录音")
        print("  audio_play <filepath> - 播放音频文件")
        print("  run_script oled - 运行OLED显示脚本")
        
        # 主线程保持运行
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
    finally:
        cleanup()
        print("系统已关闭")

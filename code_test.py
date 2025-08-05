import socket
import struct
import time
import os
from tqdm import tqdm

class PiControllerTester:
    def __init__(self, pi_ip, udp_port=8888, tcp_port=8890):
        self.pi_ip = pi_ip
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.settimeout(5)  # 5秒超时

    def send_udp_command(self, command):
        """发送UDP命令并接收响应"""
        try:
            self.udp_socket.sendto(command.encode(), (self.pi_ip, self.udp_port))
            response, _ = self.udp_socket.recvfrom(1024)
            return response.decode()
        except socket.timeout:
            return "Error: No response from Pi (timeout)"
        except Exception as e:
            return f"Error: {str(e)}"

    def send_file_via_tcp(self, filepath):
        """通过TCP发送文件到树莓派"""
        if not os.path.exists(filepath):
            return "Error: File not found"
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.pi_ip, self.tcp_port))
                filesize = os.path.getsize(filepath)
                filename = os.path.basename(filepath)
                
                # 发送文件头 (4字节文件大小 + 文件名)
                header = struct.pack('>I', filesize) + filename.encode()
                sock.sendall(header)
                
                # 发送文件内容
                with open(filepath, 'rb') as f:
                    with tqdm(total=filesize, unit='B', unit_scale=True, 
                             desc=f"Sending {filename}") as pbar:
                        while True:
                            chunk = f.read(4096)
                            if not chunk:
                                break
                            sock.sendall(chunk)
                            pbar.update(len(chunk))
                
                # 等待确认
                response = sock.recv(1024)
                return response.decode()
        except Exception as e:
            return f"Error: {str(e)}"

    def test_camera(self):
        """测试摄像头功能"""
        print("\n=== 测试摄像头功能 ===")
        print("1. 拍摄单张照片")
        print("2. 开始连续拍摄(间隔2秒)")
        print("3. 停止连续拍摄")
        print("4. 录制视频(10秒)")
        choice = input("请选择测试项(1-4): ")
        
        if choice == "1":
            return self.send_udp_command("capture")
        elif choice == "2":
            return self.send_udp_command("continuous_capture 2")
        elif choice == "3":
            return self.send_udp_command("continuous_capture stop")
        elif choice == "4":
            return self.send_udp_command("record 10")
        else:
            return "无效选择"

    def test_audio(self):
        """测试音频功能"""
        print("\n=== 测试音频功能 ===")
        print("1. 开始录音")
        print("2. 停止录音")
        print("3. 播放音频(需要先上传wav文件)")
        choice = input("请选择测试项(1-3): ")
        
        if choice == "1":
            return self.send_udp_command("audio_record_start")
        elif choice == "2":
            return self.send_udp_command("audio_record_stop")
        elif choice == "3":
            filename = input("输入要播放的音频文件名(在树莓派上): ")
            return self.send_udp_command(f"audio_play {filename}")
        else:
            return "无效选择"

    def test_gpio(self):
        """测试GPIO功能"""
        print("\n=== 测试GPIO功能 ===")
        print("1. 打开LED")
        print("2. 关闭LED")
        choice = input("请选择测试项(1-2): ")
        
        if choice == "1":
            return self.send_udp_command("led_on")
        elif choice == "2":
            return self.send_udp_command("led_off")
        else:
            return "无效选择"

    def test_hdmi(self):
        """测试HDMI功能"""
        print("\n=== 测试HDMI功能 ===")
        print("1. 播放视频(需要先上传mp4文件)")
        print("2. 停止播放")
        choice = input("请选择测试项(1-2): ")
        
        if choice == "1":
            filename = input("输入要播放的视频文件名(在树莓派上): ")
            return self.send_udp_command(f"hdmi_play {filename}")
        elif choice == "2":
            return self.send_udp_command("hdmi_stop")
        else:
            return "无效选择"

    def test_file_transfer(self):
        """测试文件传输功能"""
        print("\n=== 测试文件传输功能 ===")
        print("1. 上传文件到树莓派")
        print("2. 从树莓派下载文件")
        choice = input("请选择测试项(1-2): ")
        
        if choice == "1":
            filepath = input("输入要上传的文件路径: ")
            return self.send_file_via_tcp(filepath)
        elif choice == "2":
            filename = input("输入要下载的文件名(在树莓派上): ")
            return self.send_udp_command(f"download {filename}")
        else:
            return "无效选择"

    def run_tests(self):
        """运行测试菜单"""
        while True:
            print("\n=== 树莓派控制测试程序 ===")
            print("1. 测试摄像头功能")
            print("2. 测试音频功能")
            print("3. 测试GPIO功能")
            print("4. 测试HDMI功能")
            print("5. 测试文件传输功能")
            print("6. 测试OLED显示")
            print("7. 测试串口通信")
            print("8. 设置系统时间")
            print("0. 退出")
            
            choice = input("请选择测试项目(0-8): ")
            
            if choice == "0":
                break
            elif choice == "1":
                result = self.test_camera()
            elif choice == "2":
                result = self.test_audio()
            elif choice == "3":
                result = self.test_gpio()
            elif choice == "4":
                result = self.test_hdmi()
            elif choice == "5":
                result = self.test_file_transfer()
            elif choice == "6":
                result = self.send_udp_command("run_script oled")
            elif choice == "7":
                port = input("输入串口设备(如/dev/ttyACM0): ")
                data = input("输入要发送的数据: ")
                result = self.send_udp_command(f"serial {port} {data}")
            elif choice == "8":
                time_str = input("输入时间(格式: YYYY-MM-DD HH:MM:SS): ")
                result = self.send_udp_command(f"set_time {time_str}")
            else:
                result = "无效选择"
                continue
            
            print("\n=== 测试结果 ===")
            print(result)
            input("\n按Enter键继续...")


if __name__ == "__main__":
    pi_ip = input("请输入树莓派的IP地址: ")
    tester = PiControllerTester(pi_ip)
    tester.run_tests()

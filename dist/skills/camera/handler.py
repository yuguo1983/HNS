import cv2
from datetime import datetime
from pathlib import Path

# 全局变量，用于控制录像状态
_recording = False
_video_writer = None
_current_cam = None


def handle_camera(action="", camera_index=0, save_path=""):
    """
    摄像头控制 - 拍照、录像、打开预览

    action: "capture" | "record" | "stop" | "preview"
    camera_index: 摄像头编号，默认 0
    save_path: 保存路径（可选）
    """
    global _recording, _video_writer, _current_cam

    cap = None
    try:
        if action == "open" or action == "preview":
            # 打开摄像头预览窗口
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return "[错误] 无法打开摄像头，请检查摄像头是否连接"

            _current_cam = cap
            window_name = f"Camera - Press 's' to save photo, 'q' to quit"
            cv2.namedWindow(window_name)

            while True:
                ret, frame = cap.read()
                if not ret:
                    return "[错误] 无法读取摄像头画面"

                # 显示录像状态
                if _recording:
                    cv2.putText(frame, "REC", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

                cv2.imshow(window_name, frame)
                key = cv2.waitKey(1) & 0xFF

                if key == ord('q'):
                    break
                elif key == ord('s'):
                    # 拍照
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    photo_path = save_path or f"photo_{timestamp}.jpg"
                    cv2.imwrite(photo_path, frame)
                    return f"📸 照片已保存: {photo_path}"

            cv2.destroyAllWindows()
            return "[info] 摄像头已关闭"

        elif action == "capture" or action == "photo":
            # 拍照（单次）
            if _current_cam is None or not _current_cam.isOpened():
                cap = cv2.VideoCapture(camera_index)
                if not cap.isOpened():
                    return "[错误] 无法打开摄像头"
                _current_cam = cap

            ret, frame = _current_cam.read()
            if not ret:
                return "[错误] 无法读取摄像头画面"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            photo_path = save_path or f"photo_{timestamp}.jpg"
            cv2.imwrite(photo_path, frame)
            return f"📸 照片已保存: {photo_path}"

        elif action == "record" or action == "start_record":
            # 开始录像
            if _current_cam is None or not _current_cam.isOpened():
                cap = cv2.VideoCapture(camera_index)
                if not cap.isOpened():
                    return "[错误] 无法打开摄像头"
                _current_cam = cap

            if _recording:
                return "[info] 正在录像中..."

            # 获取视频参数
            fps = 20.0
            width = int(_current_cam.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(_current_cam.get(cv2.CAP_PROP_FRAME_HEIGHT))

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            video_path = save_path or f"video_{timestamp}.avi"

            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            _video_writer = cv2.VideoWriter(video_path, fourcc, fps, (width, height))
            _recording = True

            return f"🔴 开始录像: {video_path} (按 'stop' 停止)"

        elif action == "stop" or action == "stop_record":
            # 停止录像
            if not _recording:
                return "[info] 未在录像"

            _recording = False
            if _video_writer:
                _video_writer.release()
                _video_writer = None

            return f"⏹️ 录像已停止并保存"

        else:
            return """[用法说明]
camera 技能支持以下操作:
- action='open': 打开摄像头预览窗口（按 's' 拍照，'q' 退出）
- action='capture': 单次拍照
- action='record': 开始录像
- action='stop': 停止录像

示例:
  handle_camera(action='open')
  handle_camera(action='capture', save_path='my_photo.jpg')
  handle_camera(action='record')
  handle_camera(action='stop')
"""

    except Exception as e:
        return f"[错误] 摄像头操作失败: {e}"
    finally:
        if cap and cap.isOpened():
            pass  # 保持摄像头开启供下次使用


# 调试入口
if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "help"
    if action == "help":
        print("Usage: python handler.py <action>")
        print("Actions: open, capture, record, stop")
    else:
        print(handle_camera(action=action))
import base64
from pathlib import Path


def handle_image_analysis(image_path="", action=""):
    """
    综合图像分析 - 图像理解 + 物体检测 + OCR + 人脸检测
    """
    # 检查 Pillow 是否安装
    try:
        from PIL import Image
    except ImportError:
        return "[错误] 需要安装 Pillow: pip install Pillow"

    # 检查 OPENCV 是否安装
    try:
        import cv2
    except ImportError:
        return "[错误] 需要安装 OpenCV: pip install opencv-python"

    path = Path(image_path)
    if not path.exists():
        return f"[错误] 图像文件不存在: {image_path}"

    try:
        # 读取图像
        img = Image.open(path)
        img_cv = cv2.imread(str(path))

        results = [f"📊 图像分析: {path.name}"]
        results.append(f"📐 尺寸: {img.width}x{img.height}, 格式: {img.format}")

        # 1. OCR 文字识别
        try:
            import pytesseract
            ocr_text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            if ocr_text.strip():
                results.append("\n📝 OCR 文字识别:")
                results.append(ocr_text.strip()[:500])
            else:
                results.append("\n📝 OCR: 未检测到文字")
        except ImportError:
            results.append("\n📝 OCR: 未安装 pytesseract (pip install pytesseract)")
        except Exception as e:
            results.append(f"\n📝 OCR: {str(e)[:100]}")

        # 2. 人脸检测
        try:
            face_cascade = cv2.CascadeClassifier(
                cv2.data_haarcascades + 'haarcascade_frontalface_default.xml'
            )
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 4)

            if len(faces) > 0:
                results.append(f"\n👤 人脸检测: 发现 {len(faces)} 张人脸")
                for i, (x, y, w, h) in enumerate(faces, 1):
                    results.append(f"   人脸 {i}: 位置({x},{y}), 大小 {w}x{h}")
            else:
                results.append("\n👤 人脸检测: 未检测到人脸")
        except Exception as e:
            results.append(f"\n👤 人脸检测: {str(e)[:100]}")

        # 3. 颜色分析（简单统计）
        try:
            pixels = img_cv.reshape(-1, 3)
            avg_color = pixels.mean(axis=0)
            results.append(f"\n🎨 平均颜色: BGR({int(avg_color[0])},{int(avg_color[1])},{int(avg_color[2])})")
        except Exception as e:
            results.append(f"\n🎨 颜色分析: {str(e)[:100]}")

        # 4. 图像基础信息
        if img.mode == 'RGB':
            results.append(f"   颜色通道: RGB")
        elif img.mode == 'L':
            results.append(f"   颜色通道: 灰度")
        else:
            results.append(f"   颜色通道: {img.mode}")

        return "\n".join(results)

    except Exception as e:
        return f"[错误] 图像分析失败: {e}"


# 调试入口
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "test.png"
    print(handle_image_analysis(path))
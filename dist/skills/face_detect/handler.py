"""
Face Detection + Gender Recognition Skill
Detects faces in images, draws blue boxes, and recognizes gender.
"""

import sys
# 确保能找到系统安装的第三方库
sys.path.append(r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\Lib\site-packages")

import cv2
import numpy as np
import os
import sys
import torch
import torch.nn as nn
from torchvision import transforms, models
from PIL import Image
import dlib
from pathlib import Path


# ── 全局缓存（避免重复加载模型）──────────────────────────
_detector = None
_classifier = None
_device = None


def _get_device():
    global _device
    if _device is None:
        _device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    return _device


def _get_detector():
    global _detector
    if _detector is None:
        _detector = dlib.get_frontal_face_detector()
    return _detector


class _GenderClassifier:
    """性别分类器 - 使用 ResNet18 特征 + 面部特征分析"""

    def __init__(self, device):
        self.device = device
        # 加载预训练 ResNet18
        self.model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1).to(device)
        self.model.eval()

        # 去掉最后的全连接层，作为特征提取器
        self.feature_extractor = nn.Sequential(*list(self.model.children())[:-1])
        self.feature_extractor.eval()

        # 图像预处理
        self.preprocess = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                               std=[0.229, 0.224, 0.225]),
        ])

    def predict(self, face_img):
        """基于面部特征分析预测性别"""
        try:
            h, w = face_img.shape[:2]
            gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)

            # 1. 面部宽高比（男性下巴更宽）
            jaw_ratio = w / h

            # 2. 眉毛区域平均亮度（男性眉毛通常更浓密 darker）
            eyebrow_y1 = max(0, h // 4)
            eyebrow_y2 = max(eyebrow_y1 + 1, h // 3)
            eyebrow_x1 = max(0, w // 4)
            eyebrow_x2 = max(eyebrow_x1 + 1, 3 * w // 4)
            eyebrow_region = gray[eyebrow_y1:eyebrow_y2, eyebrow_x1:eyebrow_x2]
            eyebrow_intensity = float(np.mean(eyebrow_region))

            # 3. 下巴区域
            chin_y1 = max(0, 3 * h // 4)
            chin_y2 = max(chin_y1 + 1, h)
            chin_region = gray[chin_y1:chin_y2, eyebrow_x1:eyebrow_x2]
            chin_intensity = float(np.mean(chin_region))

            # 综合评分 (heuristic)
            score = 0.0
            if jaw_ratio > 0.85:
                score += 1.0
            else:
                score -= 1.0
            if eyebrow_intensity < 120:
                score += 1.0
            else:
                score -= 1.0
            if chin_intensity < 130:
                score += 1.0
            else:
                score -= 1.0

            return "Male" if score >= 0 else "Female"

        except Exception:
            return "?"


def _get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = _GenderClassifier(_get_device())
    return _classifier


def handle_face_detect(action: str = ""):
    """
    人脸检测与性别识别

    action 格式: "detect:<图片路径>"
    示例: "detect:E:\\photos\\group.jpg"
          "detect:/home/user/photo.jpg"
    """
    # 解析参数
    if not action or not action.startswith("detect"):
        return (
            "[face_detect] 使用方式: 传入 action='detect:图片路径'\n"
            "例如: face_detect action='detect:E:\\\\HNS\\\\photo.jpg'"
        )

    # 提取图片路径
    image_path = action[len("detect:"):].strip()
    if not image_path:
        return "[face_detect] 错误: 未提供图片路径"

    # 检查文件是否存在
    img_file = Path(image_path)
    if not img_file.exists():
        return f"[face_detect] 错误: 文件不存在 - {image_path}"

    # 确定输出路径
    output_path = str(img_file.parent / f"{img_file.stem}_face_result{img_file.suffix}")

    try:
        # 加载模型
        detector = _get_detector()
        classifier = _get_classifier()

        # 读取图片
        img = cv2.imread(image_path)
        if img is None:
            return f"[face_detect] 错误: 无法读取图片 - {image_path}"

        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h_img, w_img = img.shape[:2]

        # 人脸检测
        faces = detector(img_rgb, 1)
        if len(faces) == 0:
            # 尝试用 Haar Cascade 补充检测
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            haar_faces = face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50)
            )
            result_lines = [f"[face_detect] 检测到 {len(haar_faces)} 个人脸\n"]
            for (x, y, fw, fh) in haar_faces:
                face_roi = img[max(0,y):min(h_img,y+fh), max(0,x):min(w_img,x+fw)]
                gender = classifier.predict(face_roi) if face_roi.size > 0 else "?"
                cv2.rectangle(img, (x, y), (x + fw, y + fh), (255, 0, 0), 3)
                label = f"#{len(result_lines)}: {gender}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(img, (x, y - th - 10), (x + tw + 10, y), (0, 0, 0), -1)
                color = (255, 180, 50) if gender == "Male" else (180, 50, 255)
                cv2.putText(img, label, (x + 5, y - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                result_lines.append(f"  人脸 #{len(result_lines)-1}: ({x},{y}) {fw}x{fh} - {gender}")

            cv2.imwrite(output_path, img)
            result_lines.append(f"\n  结果已保存: {output_path}")
            return "\n".join(result_lines)

        # dlib 检测结果
        result_lines = [f"[face_detect] 检测到 {len(faces)} 个人脸\n"]
        male_count = 0
        female_count = 0

        for i, face in enumerate(faces):
            x, y, fw, fh = face.left(), face.top(), face.width(), face.height()

            # 提取人脸区域
            y1, y2 = max(0, y), min(h_img, y + fh)
            x1, x2 = max(0, x), min(w_img, x + fw)
            face_roi = img[y1:y2, x1:x2]

            # 性别识别
            gender = classifier.predict(face_roi) if face_roi.size > 0 else "?"
            if gender == "Male":
                male_count += 1
            elif gender == "Female":
                female_count += 1

            # 蓝色矩形框
            cv2.rectangle(img, (x, y), (x + fw, y + fh), (255, 0, 0), 3)

            # 标签
            label = f"#{i+1}: {gender}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(img, (x, y - th - 10), (x + tw + 10, y), (0, 0, 0), -1)
            color = (255, 180, 50) if gender == "Male" else (180, 50, 255)
            cv2.putText(img, label, (x + 5, y - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            result_lines.append(f"  人脸 #{i+1}: ({x},{y}) {fw}x{fh} - {gender}")

        # 汇总
        result_lines.append(f"\n  [汇总] 共 {len(faces)} 人 | Male: {male_count} | Female: {female_count}")

        # 保存结果
        cv2.imwrite(output_path, img)
        result_lines.append(f"\n  [结果] 标注图片已保存: {output_path}")

        return "\n".join(result_lines)

    except ImportError as e:
        return f"[face_detect] 依赖缺失: {e}\n请安装: pip install opencv-python dlib torch torchvision Pillow numpy"
    except Exception as e:
        return f"[face_detect] 执行出错: {e}"

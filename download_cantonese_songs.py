"""
粤语金曲 TOP 20 一键下载器
使用方法: python download_cantonese_songs.py
自动下载到脚本所在目录的"粤语金曲TOP20"文件夹
"""

import requests
import os
import sys

# 20首粤语金曲
SONGS = [
    ("01", "Beyond", "海阔天空"),
    ("02", "Beyond", "光辉岁月"),
    ("03", "陈慧娴", "千千阙歌"),
    ("04", "李克勤", "月半小夜曲"),
    ("05", "陈奕迅", "富士山下"),
    ("06", "陈奕迅", "浮夸"),
    ("07", "MC张天赋", "世一"),
    ("08", "MC张天赋", "反对无效"),
    ("09", "MC张天赋", "老派约会之必要"),
    ("10", "MC张天赋", "记忆棉"),
    ("11", "张敬轩", "隐形游乐场"),
    ("12", "张敬轩", "俏郎君"),
    ("13", "林家谦", "某种老朋友"),
    ("14", "林家谦", "孤独"),
    ("15", "谢安琪", "囍帖街"),
    ("16", "陈百强", "偏偏喜欢你"),
    ("17", "谭咏麟", "一生中最爱"),
    ("18", "张国荣", "沉默是金"),
    ("19", "王菲", "容易受伤的女人"),
    ("20", "杨千嬅", "少女的祈祷"),
]

def download_all():
    # 脚本所在目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    save_dir = os.path.join(base_dir, "粤语金曲TOP20")
    os.makedirs(save_dir, exist_ok=True)

    print("=" * 55)
    print("   粤语金曲 TOP 20 一键下载")
    print(f"   保存到: {save_dir}")
    print("=" * 55)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    success = 0
    failed = 0

    for num, artist, title in SONGS:
        filename = f"{num}. {artist} - {title}.mp3"
        filepath = os.path.join(save_dir, filename)

        # 跳过已存在的
        if os.path.exists(filepath) and os.path.getsize(filepath) > 10000:
            print(f"  [OK] 已存在: {filename}")
            success += 1
            continue

        print(f"  [下载] {filename}...", end=" ")

        try:
            # 搜索歌曲ID
            resp = requests.post(
                "https://music.163.com/api/search/get",
                data={"s": f"{artist} {title}", "type": 1, "limit": 1},
                headers=headers,
                timeout=10
            )
            result = resp.json()
            songs = result.get("result", {}).get("songs", [])

            if not songs:
                print("未找到")
                failed += 1
                continue

            song_id = songs[0].get("id")

            # 下载音频
            dl_url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"
            dl_resp = requests.get(dl_url, headers=headers, timeout=30)

            if dl_resp.status_code == 200 and len(dl_resp.content) > 10000:
                with open(filepath, "wb") as f:
                    f.write(dl_resp.content)
                size = len(dl_resp.content) / 1024 / 1024
                print(f"OK ({size:.1f}MB)")
                success += 1
            else:
                print("下载失败")
                failed += 1

        except Exception as e:
            print(f"错误: {e}")
            failed += 1

    print("\n" + "=" * 55)
    print(f"   完成! 成功: {success} 首, 失败: {failed} 首")
    print(f"   文件夹: {save_dir}")
    print("=" * 55)

    # 列出文件
    files = sorted([f for f in os.listdir(save_dir) if f.endswith('.mp3')])
    if files:
        print("\n歌曲列表:")
        for f in files:
            fp = os.path.join(save_dir, f)
            sz = os.path.getsize(fp) / 1024 / 1024
            print(f"  {f} ({sz:.1f}MB)")

    input("\n按回车键退出...")

if __name__ == "__main__":
    download_all()

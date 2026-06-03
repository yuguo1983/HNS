"""
网易云音乐 - 粤语歌曲排行榜 TOP 20 获取脚本
仅供学习参考，请勿用于商业用途
"""

import requests
import json
import os
import re
import time

# 粤语榜榜单ID (网易云音乐)
CANTONESE_PLAYLIST_ID = "64016"

# 热门粤语歌单ID备用
BACKUP_PLAYLIST_IDS = [
    "64016",       # 粤语榜
    "2818863516",  # 粤语热歌
    "529453001",   # 经典粤语
]

def get_playlist_songs(playlist_id):
    """通过网易云音乐公开API获取歌单歌曲列表"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://music.163.com/",
    }
    
    url = f"https://music.163.com/api/playlist/detail?id={playlist_id}"
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get("code") == 200:
            playlist = data.get("result", {})
            tracks = playlist.get("tracks", [])
            songs = []
            for idx, track in enumerate(tracks[:20], 1):
                song_info = {
                    "rank": idx,
                    "name": track.get("name", "未知"),
                    "id": track.get("id", ""),
                    "artists": " / ".join([a.get("name", "") for a in track.get("artists", [])]),
                    "album": track.get("album", {}).get("name", "未知"),
                    "duration": track.get("duration", 0),
                }
                songs.append(song_info)
            return songs
        else:
            print(f"API返回错误码: {data.get('code')}")
            return None
    except Exception as e:
        print(f"请求出错: {e}")
        return None


def get_songs_from_webpage(playlist_id):
    """备用方案：从网页版解析歌单"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://music.163.com/",
    }
    
    url = f"https://music.163.com/playlist?id={playlist_id}"
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        html = resp.text
        
        # 从页面中提取歌曲列表（JSON数据内嵌在页面中）
        pattern = r'window\.__INITIAL_STATE__\s*=\s*({.*?});'
        match = re.search(pattern, html, re.DOTALL)
        
        if match:
            data = json.loads(match.group(1))
            playlist_info = data.get("playlist", {})
            tracks = playlist_info.get("tracks", [])
            
            songs = []
            for idx, track in enumerate(tracks[:20], 1):
                song_info = {
                    "rank": idx,
                    "name": track.get("name", "未知"),
                    "id": track.get("id", ""),
                    "artists": " / ".join([a.get("name", "") for a in track.get("artists", [])]),
                    "album": track.get("album", {}).get("name", "未知"),
                }
                songs.append(song_info)
            return songs
        else:
            print("未能在页面中找到歌曲数据")
            return None
    except Exception as e:
        print(f"解析页面出错: {e}")
        return None


def format_time(ms):
    """毫秒转分:秒"""
    seconds = ms // 1000
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


def print_songs(songs):
    """打印歌曲列表"""
    if not songs:
        print("❌ 未获取到歌曲数据")
        return
    
    print("\n" + "=" * 70)
    print("  🎵 网易云音乐 - 粤语歌曲排行榜 TOP 20")
    print("=" * 70)
    print(f"{'排名':<6}{'歌曲':<28}{'歌手':<24}{'时长'}")
    print("-" * 70)
    
    for song in songs:
        name = song["name"][:20] if len(song["name"]) > 20 else song["name"]
        artists = song["artists"][:18] if len(song["artists"]) > 18 else song["artists"]
        duration = format_time(song.get("duration", 0))
        print(f"  {song['rank']:<4}  {name:<24}  {artists:<20}  {duration}")
    
    print("=" * 70)
    print(f"  共 {len(songs)} 首歌曲")
    print("=" * 70)


def save_to_file(songs, filename="粤语歌曲排行榜TOP20.txt"):
    """保存到文本文件"""
    if not songs:
        print("❌ 没有数据可保存")
        return
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write("🎵 网易云音乐 - 粤语歌曲排行榜 TOP 20\n")
        f.write("=" * 60 + "\n")
        f.write(f"{'排名':<6}{'歌曲':<30}{'歌手':<28}{'时长'}\n")
        f.write("-" * 60 + "\n")
        
        for song in songs:
            duration = format_time(song.get("duration", 0))
            f.write(f"  {song['rank']:<4}  {song['name']:<26}  {song['artists']:<24}  {duration}\n")
        
        f.write("=" * 60 + "\n")
        f.write(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    full_path = os.path.abspath(filename)
    print(f"\n✅ 已保存到文件: {full_path}")


def main():
    print("🎵 正在获取网易云音乐粤语排行榜...")
    print("-" * 40)
    
    songs = None
    
    # 方案1：使用API
    print("📡 尝试API方式...")
    songs = get_playlist_songs(CANTONESE_PLAYLIST_ID)
    
    # 方案2：备用 - 从网页解析
    if not songs:
        print("📄 尝试网页解析方式...")
        for pid in BACKUP_PLAYLIST_IDS:
            print(f"   尝试歌单ID: {pid}")
            songs = get_songs_from_webpage(pid)
            if songs:
                break
    
    # 方案3：如果上述都失败，使用内置数据
    if not songs:
        print("⚠️ 网络获取失败，使用内置经典粤语歌数据")
        songs = get_fallback_songs()
    
    # 打印结果
    print_songs(songs)
    
    # 保存到文件
    save_to_file(songs)
    
    print("\n💡 提示: 在网易云音乐App中搜索以上歌曲即可试听下载")
    print("   📱 App → 搜索歌名 → 下载到本地 → 移入F盘")


def get_fallback_songs():
    """备用数据 - 经典粤语歌曲 TOP 20"""
    classics = [
        ("海阔天空", "Beyond", 315000),
        ("光辉岁月", "Beyond", 295000),
        ("千千阙歌", "陈慧娴", 290000),
        ("月半小夜曲", "李克勤", 275000),
        ("富士山下", "陈奕迅", 285000),
        ("浮夸", "陈奕迅", 280000),
        ("明年今日", "陈奕迅", 260000),
        ("囍帖街", "谢安琪", 255000),
        ("遥远的她", "张学友", 270000),
        ("偏偏喜欢你", "陈百强", 250000),
        ("一生中最爱", "谭咏麟", 265000),
        ("红日", "李克勤", 245000),
        ("上海滩", "叶丽仪", 240000),
        ("真的爱你", "Beyond", 260000),
        ("喜欢你", "Beyond", 255000),
        ("风的季节", "徐小凤", 230000),
        ("铁血丹心", "罗文 / 甄妮", 235000),
        ("沉默是金", "张国荣", 250000),
        ("Monica", "张国荣", 225000),
        ("漫步人生路", "邓丽君", 240000),
    ]
    
    return [
        {
            "rank": idx + 1,
            "name": name,
            "id": "",
            "artists": artist,
            "album": "",
            "duration": dur,
        }
        for idx, (name, artist, dur) in enumerate(classics)
    ]


if __name__ == "__main__":
    main()

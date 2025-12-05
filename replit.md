# Discord 音樂機器人

## 專案概述
這是一個功能完整的 Discord 音樂機器人，支援 YouTube 音樂播放、播放清單管理和關鍵字過濾功能。

## 專案結構
```
├── main.py                 # 主程式入口點
├── src/
│   ├── bot/
│   │   ├── commands/
│   │   │   ├── music.py       # 音樂播放指令
│   │   │   ├── playlist.py    # 播放清單指令
│   │   │   └── moderation.py  # 管理指令
│   │   ├── autocomplete.py    # 自動完成功能
│   │   ├── player.py          # 音樂播放器
│   │   └── queue.py           # 歌曲隊列管理
│   ├── data/
│   │   └── storage.py         # 資料儲存管理
│   └── utils/
│       ├── audio.py           # 音訊工具
│       ├── filters.py         # 關鍵字過濾
│       └── youtube.py         # YouTube 搜尋
└── data/                      # 資料目錄（自動建立）
    ├── playlists.json         # 播放清單資料
    └── banned_keywords.json   # 禁止關鍵字資料
```

## 功能列表

### 音樂播放指令
- `/play <query>` - 播放音樂
- `/skip` - 跳過目前歌曲
- `/pause` - 暫停播放
- `/resume` - 繼續播放
- `/stop` - 停止播放並清空待播清單
- `/queue` - 查看待播清單
- `/clear` - 清空待播清單
- `/leave` - 離開語音頻道

### 播放清單指令
- `/create-playlist <name>` - 建立播放清單
- `/delete-playlist <name>` - 刪除播放清單
- `/list-playlists` - 查看所有播放清單
- `/show-playlist <name>` - 查看播放清單內容
- `/add <playlist> <query>` - 新增歌曲到播放清單
- `/remove <playlist> <song>` - 從播放清單移除歌曲
- `/play-playlist <name>` - 播放整個播放清單

### 管理指令（需要管理權限）
- `/ban-keyword <keyword>` - 禁止特定關鍵字
- `/unban-keyword <keyword>` - 解除禁止關鍵字
- `/list-banned` - 查看所有禁止的關鍵字

## 環境變數
- `DISCORD_TOKEN` - Discord 機器人 Token（必要）

## 技術棧
- Python 3.11
- discord.py 2.x
- yt-dlp
- FFmpeg
- PyNaCl

## 部署說明
1. 在 Discord Developer Portal 建立機器人並取得 Token
2. 設定環境變數 `DISCORD_TOKEN`
3. 執行 `python main.py` 啟動機器人
4. 邀請機器人到你的伺服器

## 最近更新
- 2024-12: 初始版本建立

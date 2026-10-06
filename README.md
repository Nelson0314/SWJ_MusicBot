# SWJ 音樂機器人

Discord 音樂機器人：YouTube 播放、播放清單、猜歌遊戲、趣味互動，以及可以跟大家即時聊天的 AI。

## 啟動

1. 在 `.env` 填入 `DISCORD_TOKEN`（可參考 `.env.example`）。
2. 雙擊 `啟動機器人.bat`。它會自動安裝缺少的套件、更新 yt-dlp，然後啟動機器人。

### AI 聊天（選用）

在 `.env` 加入 `ANTHROPIC_API_KEY=你的金鑰` 並重新啟動，`/chat` 與 @機器人 就會改用 Claude 回覆，
而且可以直接用講的點歌（例如「幫我放周杰倫的晴天」）。沒有設定金鑰時，會使用內建的簡易聊天。

## 指令

### 音樂
| 指令 | 說明 |
| --- | --- |
| `/play` | 播放歌曲；也可以貼 YouTube 影片或**播放清單網址**（整份清單瞬間加入） |
| `/search` | 搜尋並從結果中選擇要播放的歌 |
| `/skip` `/pause` `/resume` `/stop` `/leave` | 基本控制 |
| `/queue [頁數]` `/clear` | 查看 / 清空待播清單 |
| `/nowplaying` | 正在播放的歌曲與進度條 |
| `/volume` | 調整音量 0~200% |
| `/loop` | 關閉 / 單曲循環 / 清單循環 |
| `/shuffle` | 打亂待播清單 |
| `/queue-remove` `/queue-move` `/skipto` | 管理待播清單 |
| `/seek` `/replay` | 跳到指定時間 / 從頭播放 |
| `/history` `/top` | 最近播放 / 點歌排行榜 |
| `/lyrics` | 查歌詞 |

「正在播放」訊息下方有 ⏯️ ⏭️ ⏹️ 🔁 🔀 按鈕可以直接操作。

### 播放清單
`/create-playlist` `/delete-playlist` `/list-playlists` `/show-playlist` `/add` `/remove` `/play-playlist`，
以及新的 `/import-playlist`（從 YouTube 播放清單匯入）與 `/save-queue`（把目前的待播存成清單）。

### 聊天
| 指令 | 說明 |
| --- | --- |
| `/chat` | 跟機器人聊天（也可以直接 @機器人 或回覆它的訊息） |
| `/chat-channel` | 讓機器人在這個頻道自動回覆所有訊息 |
| `/chat-voice` | 機器人在語音頻道時，把聊天回覆用繁中語音念出來 |
| `/say` | 讓機器人用繁中語音說一段話 |
| `/chat-reset` | 清除這個頻道的聊天記憶 |

### 趣味
`/music-quiz` 猜歌遊戲、`/fortune` 今日運勢、`/8ball` 神奇海螺、`/roll` 擲骰子、`/coin` 擲硬幣、
`/rps` 猜拳、`/choose` 幫你選、`/rate` 評分、`/compatibility` 契合度、`/poll` 投票、
`/hug` `/pat` `/poke` `/slap` 互動。

### 管理
`/ban-keyword` `/unban-keyword` `/list-banned`

## 開發

```bash
python -m unittest discover -s . -p "test_*.py"
```

測試不需要連線 Discord 或 YouTube（使用本機 ffmpeg 與模擬的串流）。

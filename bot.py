import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
import shutil
import re
import random
import time
import urllib.request
import urllib.error
import yt_dlp
from gtts import gTTS
import tempfile
from aiohttp import web

# ── config 로드 ──────────────────────────────────────────
DEFAULT_CONFIG_PATH = "config.json"
CONFIG_PATH = os.getenv("CONFIG_PATH", DEFAULT_CONFIG_PATH)
CONFIG_BACKUP_PATH = os.getenv("CONFIG_BACKUP_PATH", f"{CONFIG_PATH}.bak")
LEVELS_PATH = os.getenv("LEVELS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "levels.json"))

def ensure_config_file():
    config_dir = os.path.dirname(CONFIG_PATH)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        return
    if CONFIG_PATH != DEFAULT_CONFIG_PATH and os.path.exists(DEFAULT_CONFIG_PATH):
        shutil.copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)
        return
    raise FileNotFoundError(f"config 파일을 찾을 수 없습니다: {CONFIG_PATH}")

def load_config():
    ensure_config_file()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    ensure_config_file()
    backup_dir = os.path.dirname(CONFIG_BACKUP_PATH)
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        shutil.copyfile(CONFIG_PATH, CONFIG_BACKUP_PATH)
    temp_path = f"{CONFIG_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, CONFIG_PATH)

# ── 봇 설정 ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
INHOUSE_API_URL = os.getenv("INHOUSE_API_URL", "https://davido-inhouse-production.up.railway.app").rstrip("/")
BOT_API_SECRET = os.getenv("BOT_API_SECRET", "")
BOT_API_PORT = int(os.getenv("BOT_API_PORT") or os.getenv("PORT") or "8080")

# ── yt-dlp 옵션 ──────────────────────────────────────────
YDL_OPTS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}

FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# ── 음악 큐 ──────────────────────────────────────────────
music_queue = []
is_playing = False

# ── embed 생성 ───────────────────────────────────────────
def build_embed(user_data: dict, cfg: dict) -> discord.Embed:
    user_counts = user_data.get("counts", {})
    name = user_data.get("name", "")
    divider = "═════════════"

    roulette_rewards = user_data.get("rewards", cfg.get("rewards", []))
    sponsor_rewards = user_data.get("sponsor_rewards", cfg.get("sponsor_rewards", []))

    roulette_lines = "\n".join(
        f"# {r} : {user_counts.get(r, 0)}개"
        for r in roulette_rewards
    )
    desc = f"### 룰렛보상\n{divider}\n{roulette_lines}"

    if sponsor_rewards:
        sponsor_lines = "\n".join(
            f"# {r} : {user_counts.get(r, 0)}개"
            for r in sponsor_rewards
        )
        desc += f"\n\n### 후원 보상\n{divider}\n{sponsor_lines}"

    embed = discord.Embed(
        description=desc,
        color=int(cfg.get("embed_color", "5F93C9"), 16),
    )
    embed.set_author(name=f"{name}님의 보관함")
    image_url = cfg.get("image_url", "")
    if image_url:
        embed.set_image(url=image_url)
    return embed

# ── 포스트(스레드) 메시지 수정 ────────────────────────────
async def update_post_message(user_data: dict, cfg: dict) -> str:
    thread_id = user_data.get("thread_id")
    message_id = user_data.get("message_id")

    if not thread_id:
        return "thread_id 없음"

    try:
        thread = await bot.fetch_channel(int(thread_id))
    except Exception as e:
        return f"채널 조회 실패: {e}"

    embed = build_embed(user_data, cfg)

    if message_id:
        try:
            msg = await thread.fetch_message(int(message_id))
            await msg.edit(embed=embed)
            return "수정 완료"
        except discord.NotFound:
            message_id = None

    if not message_id:
        try:
            async for msg in thread.history(limit=50):
                if msg.author.id == bot.user.id and msg.embeds:
                    await msg.edit(embed=embed)
                    user_data["message_id"] = str(msg.id)
                    return f"기존 메시지 수정 완료 (ID: {msg.id})"
        except Exception:
            pass

    try:
        sent = await thread.send(embed=embed)
        user_data["message_id"] = str(sent.id)
        return f"신규전송 (메시지ID: {sent.id})"
    except Exception as e:
        return f"전송 실패: {e}"

# ── 이벤트 ───────────────────────────────────────────────
command_sync_done = False
register_button_view_added = False
bot_api_runner = None

@bot.event
async def setup_hook():
    global register_button_view_added
    if not register_button_view_added:
        bot.add_view(InhouseRegisterButtonView())
        register_button_view_added = True
    await start_bot_api()

@bot.event
async def on_ready():
    global command_sync_done
    if command_sync_done:
        print(f"봇 재연결: {bot.user} (명령어 동기화 건너뜀)")
        return
    try:
        # 기존 서버 전용 명령어 제거 (전역 전환 후 중복 방지)
        guild = discord.Object(id=1428066375334756354)
        tree.clear_commands(guild=guild)
        await tree.sync(guild=guild)
        # 전역 동기화
        synced = await tree.sync()
        print(f"봇 온라인: {bot.user} (ID: {bot.user.id})")
        print(f"슬래시 명령어 전역 동기화 완료: {len(synced)}개")
        command_sync_done = True
        for cmd in synced:
            print(f"  - /{cmd.name}")
    except Exception as e:
        print(f"명령어 동기화 실패: {e}")

@bot.command(name="sync")
async def sync_commands(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 관리자 권한이 필요합니다.")
        return
    try:
        synced = await tree.sync()  # 전역 동기화
        names = "\n".join(f"• /{cmd.name}" for cmd in synced)
        await ctx.send(f"✅ 슬래시 명령어 **{len(synced)}개** 전역 동기화 완료!\n{names}")
    except Exception as e:
        await ctx.send(f"❌ 동기화 실패: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    # XP 지급 (레벨 시스템) - 논블로킹으로 실행
    asyncio.create_task(_grant_xp(message))

    vc = message.guild.voice_client if message.guild else None
    if not vc or not vc.is_connected():
        return

    # 봇이 있는 음성채널과 연결된 텍스트채널인지 확인
    voice_channel = vc.channel
    if not hasattr(voice_channel, 'id'):
        return

    # 음성채널 자체 텍스트 또는 같은 이름의 텍스트채널 확인
    is_voice_text = (
        # 음성채널 안의 텍스트 (스테이지/음성채널 내장 채팅)
        getattr(message.channel, 'id', None) == voice_channel.id or
        # 음성채널과 같은 이름의 텍스트채널
        message.channel.name == voice_channel.name
    )

    if not is_voice_text:
        return

    # 명령어는 읽지 않음
    if message.content.startswith('/') or message.content.startswith('!'):
        return

    # 너무 긴 메시지는 50자로 자름
    text = message.content[:50]
    if not text.strip():
        return

    try:
        tts = gTTS(text=text, lang="ko")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tts.save(tmp.name)
        tmp.close()

        # 현재 재생 중이면 대기
        while vc.is_playing():
            await asyncio.sleep(0.5)

        def after_auto_tts(error):
            os.unlink(tmp.name)

        vc.play(discord.FFmpegPCMAudio(tmp.name), after=after_auto_tts)
    except Exception:
        pass

# ── 관리자 권한 체크 ──────────────────────────────────────
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

def normalize_tier(raw: str) -> str:
    text = (raw or "").strip().lower().replace(" ", "").replace("lp", "")
    if not text:
        return "GO4"
    aliases = {
        "챌": "CH", "챌린저": "CH", "첼린저": "CH", "챌린져": "CH", "첼린져": "CH", "c": "CH", "challenger": "CH",
        "그마": "GM", "그랜드마스터": "GM", "그랜드": "GM", "gm": "GM", "grandmaster": "GM",
        "마스터": "MS", "마": "MS", "m": "MS", "master": "MS",
        "다이아": "DI", "다이아몬드": "DI", "다야": "DI", "다": "DI", "d": "DI", "diamond": "DI",
        "에메랄드": "EM", "에매랄드": "EM", "애매랄드": "EM", "애메랄드": "EM", "에메": "EM", "에": "EM", "e": "EM", "emerald": "EM",
        "플래티넘": "PL", "플레티넘": "PL", "플래": "PL", "플레": "PL", "플": "PL", "p": "PL", "platinum": "PL",
        "골드": "GO", "골": "GO", "g": "GO", "gold": "GO",
        "실버": "SI", "실": "SI", "s": "SI", "silver": "SI",
        "브론즈": "BR", "브": "BR", "b": "BR", "bronze": "BR",
        "아이언": "IR", "아": "IR", "i": "IR", "iron": "IR",
    }
    for name in sorted(aliases, key=len, reverse=True):
        code = aliases[name]
        if text.startswith(name):
            rest = text.replace(name, "", 1)
            if code in ("CH", "GM"):
                return code
            if code == "MS":
                if rest.isdigit():
                    lp = max(0, min(800, round(int(rest) / 100) * 100))
                    return f"MS{lp}"
                return "MS0"
            if rest in ("1", "2", "3", "4"):
                return code + rest
            return code + "4"
    return "GO4"

def normalize_position(raw: str) -> str:
    text = (raw or "").strip().lower().replace(" ", "")
    table = {
        "탑": "탑", "탑솔": "탑", "탑솔러": "탑", "탑라인": "탑", "탑라이너": "탑",
        "top": "탑", "t": "탑",
        "정글": "정글", "정글러": "정글", "정": "정글",
        "jg": "정글", "jgl": "정글", "jungle": "정글", "jungler": "정글", "j": "정글",
        "미드": "미드", "미드라이너": "미드", "미": "미드",
        "mid": "미드", "middle": "미드", "m": "미드",
        "원딜": "원딜", "원딜러": "원딜", "원거리딜러": "원딜", "딜러": "원딜",
        "바텀": "원딜", "adc": "원딜", "ad": "원딜", "bot": "원딜", "bottom": "원딜", "btm": "원딜",
        "서폿": "서포터", "서폿터": "서포터", "서포터": "서포터", "서포트": "서포터", "서": "서포터",
        "sup": "서포터", "supp": "서포터", "support": "서포터", "spt": "서포터",
        "무관": "무관", "상관없음": "무관", "아무거나": "무관", "올": "무관", "전체": "무관",
        "all": "무관", "any": "무관", "fill": "무관", "none": "무관",
    }
    return table.get(text, raw.strip() if raw and raw.strip() else "무관")

def normalize_positions(main_pos: str, sub_pos_text: str) -> list[str]:
    positions = [normalize_position(main_pos)]
    parts = [p for p in re.split(r"[,/|·\s]+", sub_pos_text or "") if p.strip()]
    for part in parts:
        if len(positions) >= 3:
            break
        positions.append(normalize_position(part))
    while len(positions) < 3:
        positions.append("무관")
    return positions

def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read().decode("utf-8"))

def default_discord_config() -> dict:
    return {
        "registerButtonEnabled": True,
        "recentPlacementEnabled": True,
        "channelOverrides": {},
        "buttonOnly": False,
        "plainMessage": False,
        "buttonLabel": "내전 참가 등록",
        "buttonStyle": "primary",
        "panelTitle": "내전 참가 등록",
        "panelDescription": (
            "내전 참가 등록은 전체 정보를 입력하고, 디코 연동은 기존 DB 유저가 치지직 닉네임만 입력해 "
            "디스코드 ID를 연결합니다."
        ),
        "messageContent": (
            "# 내전 참가 등록\n"
            "처음 등록하는 사람은 내전 참가 등록 버튼을 눌러주세요.\n"
            "이미 시청자 DB에 등록된 사람은 디코 연동 버튼을 누르고 치지직 닉네임만 입력하면 "
            "음성방 이동 기능을 사용할 수 있습니다."
        ),
    }

async def fetch_discord_config() -> dict:
    try:
        config = await asyncio.to_thread(request_json, f"{INHOUSE_API_URL}/api/discord-config")
        return {**default_discord_config(), **(config or {})}
    except Exception:
        return default_discord_config()

def discord_button_style(value: str):
    return {
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
        "secondary": discord.ButtonStyle.secondary,
    }.get(value, discord.ButtonStyle.primary)

def discord_recent_enabled(config: dict, channel_id: int | None) -> bool:
    overrides = config.get("channelOverrides") if isinstance(config, dict) else {}
    channel_config = None
    if isinstance(overrides, dict) and channel_id is not None:
        channel_config = overrides.get(str(channel_id))
    if isinstance(channel_config, dict) and "recentPlacementEnabled" in channel_config:
        return channel_config.get("recentPlacementEnabled") is not False
    return config.get("recentPlacementEnabled") is not False

def build_register_message_kwargs(config: dict) -> dict:
    view = InhouseRegisterButtonView(config.get("buttonLabel", "내전 참가 등록"), config.get("buttonStyle", "primary"))
    if config.get("buttonOnly") is True:
        return {"view": view}
    if config.get("plainMessage") is True:
        content = str(
            config.get("messageContent")
            or config.get("panelDescription")
            or default_discord_config()["messageContent"]
        ).strip()
        return {"content": content[:2000], "view": view}
    embed = discord.Embed(
        title=str(config.get("panelTitle") or "내전 참가 등록")[:256],
        description=str(config.get("panelDescription") or default_discord_config()["panelDescription"])[:4096],
        color=0x5F93C9,
    )
    return {"embed": embed, "view": view}

async def send_register_button_to_channel(channel_id: int, config: dict):
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        channel = await bot.fetch_channel(int(channel_id))
    if not hasattr(channel, "send"):
        raise ValueError("메시지를 보낼 수 없는 채널입니다.")
    return await channel.send(**build_register_message_kwargs(config))

async def handle_register_button_api(request: web.Request):
    if not BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "BOT_API_SECRET이 설정되지 않았습니다."}, status=503)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "잘못된 요청입니다."}, status=400)
    if str(data.get("secret", "")) != BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "인증 실패"}, status=403)
    channel_id = str(data.get("channelId", "")).strip()
    if not channel_id.isdigit():
        return web.json_response({"ok": False, "error": "채널 ID가 필요합니다."}, status=400)
    config = await fetch_discord_config()
    if not discord_recent_enabled(config, int(channel_id)):
        return web.json_response({"ok": False, "error": "이 채널은 버튼 생성 OFF 상태입니다."}, status=403)
    try:
        msg = await send_register_button_to_channel(int(channel_id), config)
        return web.json_response({"ok": True, "messageId": str(msg.id)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def fetch_discord_channel(channel_id: int):
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        channel = await bot.fetch_channel(int(channel_id))
    return channel

async def handle_move_voice_teams_api(request: web.Request):
    if not BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "BOT_API_SECRET이 설정되지 않았습니다."}, status=503)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "잘못된 요청입니다."}, status=400)
    if str(data.get("secret", "")) != BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "인증 실패"}, status=403)

    def clean_id(value):
        text = re.sub(r"\D", "", str(value or ""))
        return int(text) if text else 0

    lobby_id = clean_id(data.get("lobbyChannelId"))
    blue_id = clean_id(data.get("blueChannelId"))
    red_id = clean_id(data.get("redChannelId"))
    blue_members = [clean_id(v) for v in data.get("blueDiscordIds", []) if clean_id(v)]
    red_members = [clean_id(v) for v in data.get("redDiscordIds", []) if clean_id(v)]
    if not lobby_id or not blue_id or not red_id:
        return web.json_response({"ok": False, "error": "음성채널 ID가 필요합니다."}, status=400)
    if len({lobby_id, blue_id, red_id}) != 3:
        return web.json_response({"ok": False, "error": "내전대기방, 1팀, 2팀 음성채널은 서로 달라야 합니다."}, status=400)

    skipped = []
    duplicate_members = set(blue_members) & set(red_members)
    if duplicate_members:
        blue_members = [member_id for member_id in blue_members if member_id not in duplicate_members]
        red_members = [member_id for member_id in red_members if member_id not in duplicate_members]
        skipped.extend(
            {"discordId": str(member_id), "reason": "duplicate_team_assignment"}
            for member_id in sorted(duplicate_members)
        )

    try:
        lobby = await fetch_discord_channel(lobby_id)
        blue_channel = await fetch_discord_channel(blue_id)
        red_channel = await fetch_discord_channel(red_id)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"채널 조회 실패: {e}"}, status=400)
    if not hasattr(lobby, "guild") or not hasattr(blue_channel, "members") or not hasattr(red_channel, "members"):
        return web.json_response({"ok": False, "error": "등록된 채널이 음성채널이 아닙니다."}, status=400)

    guild = lobby.guild
    moved = {"blue": 0, "red": 0}

    async def move_group(member_ids, target_channel, team_name):
        seen = set()
        for member_id in member_ids:
            if member_id in seen:
                skipped.append({"discordId": str(member_id), "reason": "duplicate_team_assignment"})
                continue
            seen.add(member_id)
            try:
                member = guild.get_member(member_id) or await guild.fetch_member(member_id)
                current = getattr(getattr(member, "voice", None), "channel", None)
                if current and current.id == target_channel.id:
                    moved[team_name] += 1
                    continue
                if not current or current.id != lobby.id:
                    skipped.append({"discordId": str(member_id), "reason": "not_in_lobby"})
                    continue
                await member.move_to(target_channel, reason="내전사이트 팀 음성방 이동")
                moved[team_name] += 1
            except discord.Forbidden:
                skipped.append({"discordId": str(member_id), "reason": "missing_permission"})
            except Exception as e:
                skipped.append({"discordId": str(member_id), "reason": str(e)})

    await move_group(blue_members, blue_channel, "blue")
    await move_group(red_members, red_channel, "red")
    return web.json_response({"ok": True, "moved": moved, "skipped": skipped})

async def handle_bot_command_api(request: web.Request):
    if not BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "BOT_API_SECRET이 설정되지 않았습니다."}, status=503)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "잘못된 요청입니다."}, status=400)
    if str(data.get("secret", "")) != BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "인증 실패"}, status=403)

    command = str(data.get("command", "")).strip()
    opts = data.get("options", {})

    try:
        cfg = load_config()

        if command == "차감":
            닉네임 = str(opts.get("닉네임", "")).strip()
            보상이름 = str(opts.get("보상이름", "")).strip()
            개수 = int(opts.get("개수", 0))
            if 개수 <= 0:
                return web.json_response({"ok": False, "error": "개수는 1 이상이어야 합니다."})
            target, error = find_user_reward(cfg, 닉네임, 보상이름)
            if error:
                return web.json_response({"ok": False, "error": error})
            counts = target.setdefault("counts", {})
            before = counts.get(보상이름, 0)
            after = max(0, before - 개수)
            counts[보상이름] = after
            save_config(cfg)
            result = await update_post_message(target, cfg)
            return web.json_response({"ok": True, "message": f"✅ `{닉네임}` 의 `{보상이름}` {before}개 → {after}개 ({개수}개 차감)\n포스트: {result}"})

        elif command == "추가":
            닉네임 = str(opts.get("닉네임", "")).strip()
            보상이름 = str(opts.get("보상이름", "")).strip()
            개수 = int(opts.get("개수", 0))
            if 개수 <= 0:
                return web.json_response({"ok": False, "error": "개수는 1 이상이어야 합니다."})
            target, error = find_user_reward(cfg, 닉네임, 보상이름)
            if error:
                return web.json_response({"ok": False, "error": error})
            counts = target.setdefault("counts", {})
            before = counts.get(보상이름, 0)
            after = before + 개수
            counts[보상이름] = after
            save_config(cfg)
            result = await update_post_message(target, cfg)
            return web.json_response({"ok": True, "message": f"✅ `{닉네임}` 의 `{보상이름}` {before}개 → {after}개 ({개수}개 추가)\n포스트: {result}"})

        elif command == "보상현황":
            닉네임 = str(opts.get("닉네임", "")).strip()
            target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
            if not target:
                return web.json_response({"ok": False, "error": f"유저 `{닉네임}` 를 찾을 수 없습니다."})
            counts = target.get("counts", {})
            rewards = target.get("rewards", cfg.get("rewards", []))
            sponsor = target.get("sponsor_rewards", cfg.get("sponsor_rewards", []))
            lines = [f"📦 {닙네임}님의 보관함"]
            lines += [f"• {r}: {counts.get(r, 0)}개" for r in rewards]
            if sponsor:
                lines += ["\n🎁 후원 보상"]
                lines += [f"• {r}: {counts.get(r, 0)}개" for r in sponsor]
            return web.json_response({"ok": True, "message": "\n".join(lines)})

        elif command == "유저목록":
            users = cfg.get("users", [])
            if not users:
                return web.json_response({"ok": True, "message": "등록된 유저가 없습니다."})
            names = [u.get("name", "?") for u in users]
            return web.json_response({"ok": True, "message": f"등록된 유저 {len(names)}명\n" + "\n".join(f"• {n}" for n in names)})

        elif command == "유저추가":
            닉네임 = str(opts.get("닉네임", "")).strip()
            스레드id = str(opts.get("스레드id", "")).strip()
            메시지id = str(opts.get("메시지id", "")).strip()
            users = cfg.setdefault("users", [])
            if any(u["name"] == 닉네임 for u in users):
                return web.json_response({"ok": False, "error": f"이미 존재하는 유저: `{닉네임}`"})
            users.append({"name": 닉네임, "thread_id": 스레드id, "message_id": 메시지id, "counts": {r: 0 for r in cfg.get("rewards", [])}})
            save_config(cfg)
            return web.json_response({"ok": True, "message": f"✅ `{닉네임}` 추가 완료"})

        elif command == "유저삭제":
            닉네임 = str(opts.get("닉네임", "")).strip()
            users = cfg.get("users", [])
            target = next((u for u in users if u["name"] == 닉네임), None)
            if not target:
                return web.json_response({"ok": False, "error": f"유저 `{닉네임}` 를 찾을 수 없습니다."})
            users.remove(target)
            save_config(cfg)
            return web.json_response({"ok": True, "message": f"✅ `{닉네임}` 삭제 완료"})

        elif command == "전체업데이트":
            users = cfg.get("users", [])
            if not users:
                return web.json_response({"ok": True, "message": "유저가 없습니다."})
            lines = []
            for u in users:
                result = await update_post_message(u, cfg)
                lines.append(f"• {u.get('name','?')} — {result}")
            save_config(cfg)  # message_id 변경 사항 저장
            return web.json_response({"ok": True, "message": "전체 업데이트 완료\n" + "\n".join(lines)})

        elif command in ("보상항목추가", "개별추가"):
            항목이름 = str(opts.get("항목이름", "")).strip()
            섹션 = str(opts.get("섹션", "rewards")).strip()
            section_name = "룰렛보상" if 섹션 == "rewards" else "후원보상"
            if command == "개별추가":
                닉네임 = str(opts.get("닉네임", "")).strip()
                target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
                if not target:
                    return web.json_response({"ok": False, "error": f"유저 `{닉네임}` 를 찾을 수 없습니다."})
                if 섹션 not in target:
                    target[섹션] = list(cfg.get(섹션, []))
                if 항목이름 in target[섹션]:
                    return web.json_response({"ok": False, "error": f"이미 존재하는 항목: `{항목이름}`"})
                target[섹션].append(항목이름)
                target.setdefault("counts", {})[항목이름] = 0
                save_config(cfg)
                result = await update_post_message(target, cfg)
                return web.json_response({"ok": True, "message": f"✅ `{닉네임}` 의 `{section_name}` 에 `{항목이름}` 추가 완료\n포스트: {result}"})
            else:
                reward_list = cfg.setdefault(섹션, [])
                if 항목이름 in reward_list:
                    return web.json_response({"ok": False, "error": f"이미 존재하는 항목: `{항목이름}`"})
                reward_list.append(항목이름)
                for u in cfg.get("users", []):
                    u.setdefault("counts", {})[항목이름] = 0
                save_config(cfg)
                return web.json_response({"ok": True, "message": f"✅ `{section_name}` 에 `{항목이름}` 추가 완료"})

        elif command in ("보상항목삭제", "개별삭제"):
            항목이름 = str(opts.get("항목이름", "")).strip()
            섹션 = str(opts.get("섹션", "rewards")).strip()
            section_name = "룰렛보상" if 섹션 == "rewards" else "후원보상"
            if command == "개별삭제":
                닉네임 = str(opts.get("닉네임", "")).strip()
                target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
                if not target:
                    return web.json_response({"ok": False, "error": f"유저 `{닉네임}` 를 찾을 수 없습니다."})
                if 섹션 not in target:
                    target[섹션] = list(cfg.get(섹션, []))
                if 항목이름 not in target[섹션]:
                    return web.json_response({"ok": False, "error": f"항목 `{항목이름}` 을 찾을 수 없습니다."})
                target[섹션].remove(항목이름)
                target.get("counts", {}).pop(항목이름, None)
                save_config(cfg)
                result = await update_post_message(target, cfg)
                return web.json_response({"ok": True, "message": f"✅ `{닉네임}` 의 `{section_name}` 에서 `{항목이름}` 삭제 완료\n포스트: {result}"})
            else:
                reward_list = cfg.get(섹션, [])
                if 항목이름 not in reward_list:
                    return web.json_response({"ok": False, "error": f"항목 `{항목이름}` 을 찾을 수 없습니다."})
                reward_list.remove(항목이름)
                cfg[섹션] = reward_list
                for u in cfg.get("users", []):
                    u.get("counts", {}).pop(항목이름, None)
                save_config(cfg)
                return web.json_response({"ok": True, "message": f"✅ `{section_name}` 에서 `{항목이름}` 삭제 완료"})

        else:
            return web.json_response({"ok": False, "error": f"알 수 없는 명령어: /{command}"})

    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_send_channel_message(request: web.Request):
    if not BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "BOT_API_SECRET이 설정되지 않았습니다."}, status=503)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "잘못된 요청입니다."}, status=400)
    if str(data.get("secret", "")) != BOT_API_SECRET:
        return web.json_response({"ok": False, "error": "인증 실패"}, status=403)
    channel_id = str(data.get("channelId", "")).strip()
    content = str(data.get("content", "")).strip()
    include_button = data.get("includeRegisterButton", False)
    if not channel_id:
        return web.json_response({"ok": False, "error": "채널 ID가 필요합니다."})
    if not content:
        return web.json_response({"ok": False, "error": "내용이 비어있습니다."})
    try:
        channel = await bot.fetch_channel(int(channel_id))
    except Exception as e:
        return web.json_response({"ok": False, "error": f"채널 조회 실패: {e}"})
    try:
        if include_button:
            config = await fetch_discord_config()
            view = InhouseRegisterButtonView(
                config.get("buttonLabel", "내전 참가 등록"),
                config.get("buttonStyle", "primary")
            )
            await channel.send(content=content, view=view)
        else:
            await channel.send(content=content)
        return web.json_response({"ok": True, "message": "메시지 전송 완료"})
    except Exception as e:
        return web.json_response({"ok": False, "error": f"전송 실패: {e}"})

async def handle_get_config_api(request: web.Request):
    try:
        cfg = load_config()
        return web.json_response({"ok": True, "config": cfg})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_config_preview(request: web.Request):
    try:
        html_path = os.path.join(os.path.dirname(__file__), "config-preview.html")
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    except Exception as e:
        return web.Response(text=f"오류: {e}", status=500)

async def start_bot_api():
    global bot_api_runner
    if bot_api_runner is not None:
        return
    app = web.Application()
    app.router.add_get("/health", lambda request: web.json_response({"ok": True}))
    app.router.add_get("/api/config", handle_get_config_api)
    app.router.add_post("/api/send-channel-message", handle_send_channel_message)
    app.router.add_get("/config-preview", handle_config_preview)
    app.router.add_post("/api/inhouse-register-button", handle_register_button_api)
    app.router.add_post("/api/bot-command", handle_bot_command_api)
    app.router.add_post("/api/move-voice-teams", handle_move_voice_teams_api)
    bot_api_runner = web.AppRunner(app)
    await bot_api_runner.setup()
    site = web.TCPSite(bot_api_runner, "0.0.0.0", BOT_API_PORT)
    await site.start()
    print(f"보관함봇 API 서버 시작: 0.0.0.0:{BOT_API_PORT}")

async def register_inhouse_viewer(
    discord_id: int,
    lol_name: str,
    chzzk_name: str,
    tier: str,
    main_pos: str,
    sub_pos: str,
) -> tuple[bool, str]:
    def work():
        db = request_json(f"{INHOUSE_API_URL}/api/inhouse-db")
        viewers = db.setdefault("viewers", [])
        clean_lol = lol_name.strip()
        clean_chzzk = chzzk_name.strip()
        key_lol = clean_lol.lower()
        key_chzzk = clean_chzzk.lower()
        target = next((v for v in viewers if str(v.get("discordId", "")) == str(discord_id)), None)
        if not target:
            target = next((v for v in viewers if str(v.get("name", "")).strip().lower() == key_lol), None)
        if not target and clean_chzzk:
            target = next((v for v in viewers if str(v.get("chzzk", "")).strip().lower() == key_chzzk), None)

        is_update = target is not None
        if not target:
            next_id = max([int(v.get("id", 0) or 0) for v in viewers] + [int(db.get("vid", 0) or 0)]) + 1
            target = {"id": next_id, "added": int(discord.utils.utcnow().timestamp() * 1000)}
            viewers.append(target)

        target.update({
            "name": clean_lol,
            "chzzk": clean_chzzk,
            "tier": normalize_tier(tier),
            "positions": normalize_positions(main_pos, sub_pos),
            "discordId": str(discord_id),
        })
        db["vid"] = max(int(db.get("vid", 0) or 0), int(target.get("id", 0) or 0))
        request_json(f"{INHOUSE_API_URL}/api/inhouse-db", method="POST", payload=db)
        return is_update, target

    try:
        is_update, target = await asyncio.to_thread(work)
    except urllib.error.HTTPError as e:
        return False, f"내전사이트 API 오류: HTTP {e.code}"
    except Exception as e:
        return False, f"등록 실패: {e}"

    action = "수정" if is_update else "등록"
    return True, (
        f"내전사이트 시청자 DB {action} 완료!\n"
        f"롤닉: `{target['name']}`\n"
        f"치지직: `{target['chzzk']}`\n"
        f"티어: `{target['tier']}` / 포지션: `{', '.join(target['positions'])}`"
    )

async def link_inhouse_discord_by_chzzk(discord_id: int, chzzk_name: str) -> tuple[bool, str]:
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    def work():
        db = request_json(f"{INHOUSE_API_URL}/api/inhouse-db")
        viewers = db.setdefault("viewers", [])
        key = normalize(chzzk_name)
        if not key:
            raise ValueError("치지직 닉네임을 입력해주세요.")

        matches = [v for v in viewers if normalize(v.get("chzzk", "")) == key]
        if not matches:
            raise LookupError("같은 치지직 닉네임을 가진 시청자 DB를 찾지 못했습니다.")
        if len(matches) > 1:
            raise LookupError("같은 치지직 닉네임이 2명 이상이라 자동 연동할 수 없습니다.")

        target = matches[0]
        current_discord_id = str(target.get("discordId", "") or "").strip()
        if current_discord_id and current_discord_id != str(discord_id):
            raise PermissionError("이미 다른 디스코드 계정과 연동된 DB입니다.")

        for viewer in viewers:
            if viewer is not target and str(viewer.get("discordId", "") or "") == str(discord_id):
                viewer.pop("discordId", None)

        target["discordId"] = str(discord_id)
        request_json(f"{INHOUSE_API_URL}/api/inhouse-db", method="POST", payload=db)
        return target

    try:
        target = await asyncio.to_thread(work)
    except LookupError as e:
        return False, str(e)
    except PermissionError as e:
        return False, str(e)
    except ValueError as e:
        return False, str(e)
    except urllib.error.HTTPError as e:
        return False, f"내전사이트 API 오류: HTTP {e.code}"
    except Exception as e:
        return False, f"디코 연동 실패: {e}"

    return True, (
        "디코 연동 완료!\n"
        f"롤 닉네임: `{target.get('name', '')}`\n"
        f"치지직: `{target.get('chzzk', '')}`\n"
        "이제 내전사이트의 디코 팀방 이동 기능을 사용할 수 있습니다."
    )

class InhouseRegisterModal(discord.ui.Modal, title="내전 참가 등록"):
    lol_name = discord.ui.TextInput(label="롤 닉네임", placeholder="예: dabido#kr2", max_length=80)
    chzzk_name = discord.ui.TextInput(label="치지직 닉네임", placeholder="예: 다비도", max_length=80)
    tier = discord.ui.TextInput(label="티어", placeholder="예: E2, 에메, 다야4, 마200, 그마", max_length=30)
    main_pos = discord.ui.TextInput(label="주 포지션", placeholder="예: 정글", max_length=20)
    sub_pos = discord.ui.TextInput(label="부 포지션 1/2", placeholder="예: 미드, 원딜 / 없으면 무관", max_length=40, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, message = await register_inhouse_viewer(
            interaction.user.id,
            str(self.lol_name.value),
            str(self.chzzk_name.value),
            str(self.tier.value),
            str(self.main_pos.value),
            str(self.sub_pos.value or "무관"),
        )
        if ok and interaction.guild:
            chzzk = str(self.chzzk_name.value).strip()[:32]
            if chzzk:
                try:
                    await interaction.user.edit(nick=chzzk)
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await interaction.followup.send(("✅ " if ok else "❌ ") + message, ephemeral=True)

class InhouseDiscordLinkModal(discord.ui.Modal, title="디코 연동"):
    chzzk_name = discord.ui.TextInput(label="치지직 닉네임", placeholder="예: 다비도", max_length=80)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, message = await link_inhouse_discord_by_chzzk(
            interaction.user.id,
            str(self.chzzk_name.value),
        )
        await interaction.followup.send(("✅ " if ok else "❌ ") + message, ephemeral=True)

class InhouseRegisterButtonView(discord.ui.View):
    def __init__(self, label: str = "내전 참가 등록", style: str = "primary"):
        super().__init__(timeout=None)
        self.add_item(InhouseRegisterButton(label, style))

class InhouseRegisterButton(discord.ui.Button):
    def __init__(self, label: str = "내전 참가 등록", style: str = "primary"):
        super().__init__(
            label=(label or "내전 참가 등록")[:80],
            style=discord_button_style(style),
            custom_id="davido_inhouse_register_button",
        )

    async def callback(self, interaction: discord.Interaction):
        config = await fetch_discord_config()
        if config.get("registerButtonEnabled") is False:
            await interaction.response.send_message("지금은 내전 참가 등록 버튼이 OFF 상태입니다.", ephemeral=True)
            return
        await interaction.response.send_modal(InhouseRegisterModal())

class InhouseDiscordLinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="디코 연동",
            style=discord.ButtonStyle.success,
            custom_id="davido_inhouse_discord_link_button",
        )

    async def callback(self, interaction: discord.Interaction):
        config = await fetch_discord_config()
        if config.get("registerButtonEnabled") is False:
            await interaction.response.send_message("지금은 내전 등록/연동 버튼이 OFF 상태입니다.", ephemeral=True)
            return
        await interaction.response.send_modal(InhouseDiscordLinkModal())

@tree.command(name="내전등록", description="팝업 양식으로 내전사이트 시청자 DB에 등록합니다.")
async def inhouse_register(interaction: discord.Interaction):
    await interaction.response.send_modal(InhouseRegisterModal())

@tree.command(name="내전등록버튼", description="시청자가 누르면 내전등록 팝업이 열리는 버튼 메시지를 보냅니다.")
async def send_inhouse_register_button(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    config = await fetch_discord_config()
    if not discord_recent_enabled(config, interaction.channel_id):
        await interaction.response.send_message("이 채널은 내전사이트 디스코드 관리 탭에서 `최근 메시지 아래 버튼 생성`이 OFF 상태입니다.", ephemeral=True)
        return
    await interaction.response.send_message(**build_register_message_kwargs(config))

def find_user_reward(cfg: dict, 닉네임: str, 보상이름: str):
    target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
    if not target:
        return None, f"유저 `{닉네임}` 를 찾을 수 없습니다. `/유저목록`으로 등록된 이름을 확인해 주세요."
    all_rewards = (
        target.get("rewards", cfg.get("rewards", [])) +
        target.get("sponsor_rewards", cfg.get("sponsor_rewards", []))
    )
    if 보상이름 not in all_rewards:
        return None, f"보상 항목 `{보상이름}` 이 존재하지 않습니다."
    return target, None

# ── /차감 ─────────────────────────────────────────────
@tree.command(name="차감", description="특정 유저의 보상 개수를 차감하고 포스트 메시지를 업데이트합니다.")
@app_commands.describe(닉네임="차감할 유저 닉네임", 보상이름="차감할 보상 항목 이름", 개수="차감할 개수")
async def subtract_reward(interaction: discord.Interaction, 닉네임: str, 보상이름: str, 개수: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    if 개수 <= 0:
        await interaction.response.send_message("개수는 1 이상이어야 합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    target, error = find_user_reward(cfg, 닉네임, 보상이름)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    counts = target.setdefault("counts", {})
    before = counts.get(보상이름, 0)
    after = max(0, before - 개수)
    counts[보상이름] = after
    save_config(cfg)
    result = await update_post_message(target, cfg)
    await interaction.followup.send(
        f"✅ `{닉네임}` 의 `{보상이름}` **{before}개 → {after}개** ({개수}개 차감)\n포스트 메시지: {result}",
        ephemeral=True,
    )

# ── /추가 ─────────────────────────────────────────────
@tree.command(name="추가", description="특정 유저의 보상 개수를 추가하고 포스트 메시지를 업데이트합니다.")
@app_commands.describe(닉네임="추가할 유저 닉네임", 보상이름="추가할 보상 항목 이름", 개수="추가할 개수")
async def add_reward_count(interaction: discord.Interaction, 닉네임: str, 보상이름: str, 개수: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    if 개수 <= 0:
        await interaction.response.send_message("개수는 1 이상이어야 합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    target, error = find_user_reward(cfg, 닉네임, 보상이름)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    counts = target.setdefault("counts", {})
    before = counts.get(보상이름, 0)
    after = before + 개수
    counts[보상이름] = after
    save_config(cfg)
    result = await update_post_message(target, cfg)
    await interaction.followup.send(
        f"✅ `{닉네임}` 의 `{보상이름}` **{before}개 → {after}개** ({개수}개 추가)\n포스트 메시지: {result}",
        ephemeral=True,
    )

# ── /전체업데이트 ──────────────────────────────────────────
@tree.command(name="전체업데이트", description="모든 유저의 포스트 메시지를 현재 config 기준으로 일괄 업데이트합니다.")
async def update_all(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    users = cfg.get("users", [])
    if not users:
        await interaction.followup.send("유저가 없습니다.", ephemeral=True)
        return
    lines = []
    for u in users:
        result = await update_post_message(u, cfg)
        lines.append(f"• {u.get('name', '?')} — {result}")
    save_config(cfg)  # message_id 변경 사항 저장
    await interaction.followup.send("**전체 업데이트 완료**\n" + "\n".join(lines), ephemeral=True)

# ── /보상현황 ──────────────────────────────────────────────
@tree.command(name="보상현황", description="특정 유저의 현재 보상 현황을 확인합니다.")
@app_commands.describe(닉네임="조회할 유저 닉네임")
async def show_reward(interaction: discord.Interaction, 닉네임: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    cfg = load_config()
    target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
    if not target:
        await interaction.response.send_message(f"유저 `{닉네임}` 를 찾을 수 없습니다. `/유저목록`으로 등록된 이름을 확인해 주세요.", ephemeral=True)
        return
    embed = build_embed(target, cfg)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /유저목록 ──────────────────────────────────────────────
@tree.command(name="유저목록", description="config에 등록된 유저 닉네임 목록을 확인합니다.")
async def list_users(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    cfg = load_config()
    users = cfg.get("users", [])
    if not users:
        await interaction.response.send_message("등록된 유저가 없습니다.", ephemeral=True)
        return
    names = [u.get("name", "?") for u in users]
    text = "\n".join(f"• {name}" for name in names)
    if len(text) > 1800:
        text = text[:1800] + "\n...목록이 길어서 일부만 표시했습니다."
    await interaction.response.send_message(f"**등록된 유저 {len(names)}명**\n{text}", ephemeral=True)

# ── /유저추가 ──────────────────────────────────────────────
@tree.command(name="유저추가", description="새 유저를 config에 추가합니다.")
@app_commands.describe(닉네임="추가할 유저 닉네임", 스레드id="해당 유저 포스트(스레드) ID", 메시지id="기존 메시지 ID (없으면 비워두세요)")
async def add_user(interaction: discord.Interaction, 닉네임: str, 스레드id: str, 메시지id: str = ""):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    cfg = load_config()
    users = cfg.setdefault("users", [])
    if any(u["name"] == 닉네임 for u in users):
        await interaction.response.send_message(f"이미 존재하는 유저입니다: `{닉네임}`\n스레드 ID가 바뀐 경우 `/유저삭제 닉네임:{닉네임}` 후 다시 `/유저추가` 해주세요.", ephemeral=True)
        return
    new_user = {
        "name": 닉네임,
        "thread_id": 스레드id,
        "message_id": 메시지id,
        "counts": {r: 0 for r in cfg.get("rewards", [])},
    }
    users.append(new_user)
    save_config(cfg)
    await interaction.response.send_message(f"✅ `{닉네임}` 추가 완료", ephemeral=True)

# ── /유저삭제 ──────────────────────────────────────────────
@tree.command(name="유저삭제", description="닉네임 기준으로 기존 유저를 config에서 삭제합니다.")
@app_commands.describe(닉네임="삭제할 유저 닉네임 (스레드 ID가 달라도 닉네임으로 삭제)")
async def delete_user(interaction: discord.Interaction, 닉네임: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    cfg = load_config()
    users = cfg.get("users", [])
    target = next((u for u in users if u["name"] == 닉네임), None)
    if not target:
        await interaction.response.send_message(f"유저 `{닉네임}` 를 찾을 수 없습니다. `/유저목록`으로 등록된 이름을 확인해 주세요.", ephemeral=True)
        return
    users.remove(target)
    save_config(cfg)
    await interaction.response.send_message(f"✅ `{닉네임}` 삭제 완료", ephemeral=True)

# ── /자동등록 ──────────────────────────────────────────────
FORUM_CHANNEL_ID = 1491054022877253642

@tree.command(name="자동등록", description="포스트 채널의 스레드를 자동 스캔해서 유저 목록을 등록합니다.")
async def auto_register(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        forum = await bot.fetch_channel(FORUM_CHANNEL_ID)
    except Exception as e:
        await interaction.followup.send(f"채널 조회 실패: {e}", ephemeral=True)
        return
    cfg = load_config()
    existing_ids = {u["thread_id"] for u in cfg.get("users", [])}
    rewards = cfg.get("rewards", [])
    new_users = []
    try:
        active = await forum.guild.active_threads()
        threads = [t for t in active if t.parent_id == FORUM_CHANNEL_ID]
    except Exception as e:
        await interaction.followup.send(f"스레드 조회 실패: {e}", ephemeral=True)
        return
    try:
        async for t in forum.archived_threads(limit=100):
            if str(t.id) not in [str(th.id) for th in threads]:
                threads.append(t)
    except Exception:
        pass
    for thread in threads:
        tid = str(thread.id)
        if tid in existing_ids:
            continue
        cfg.setdefault("users", []).append({
            "name": thread.name,
            "thread_id": tid,
            "message_id": "",
            "counts": {r: 0 for r in rewards},
        })
        new_users.append(thread.name)
    save_config(cfg)
    if not new_users:
        await interaction.followup.send("새로 등록할 스레드가 없습니다.", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ **{len(new_users)}개 스레드 자동 등록 완료!**\n" + "\n".join(f"• {n}" for n in new_users),
        ephemeral=True,
    )

# ── /보상항목추가 ──────────────────────────────────────────────
@tree.command(name="보상항목추가", description="룰렛보상 또는 후원보상 섹션에 항목을 추가합니다.")
@app_commands.describe(항목이름="추가할 보상 항목 이름", 섹션="룰렛보상 또는 후원보상")
@app_commands.choices(섹션=[
    app_commands.Choice(name="룰렛보상", value="rewards"),
    app_commands.Choice(name="후원보상", value="sponsor_rewards"),
])
async def add_reward(interaction: discord.Interaction, 항목이름: str, 섹션: str = "rewards"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    reward_list = cfg.setdefault(섹션, [])
    if 항목이름 in reward_list:
        await interaction.followup.send(f"이미 존재하는 항목입니다: `{항목이름}`", ephemeral=True)
        return
    reward_list.append(항목이름)
    for u in cfg.get("users", []):
        u.setdefault("counts", {})[항목이름] = 0
    save_config(cfg)
    lines = []
    for u in cfg.get("users", []):
        result = await update_post_message(u, cfg)
        lines.append(f"• {u.get('name', '?')} — {result}")
    section_name = "룰렛보상" if 섹션 == "rewards" else "후원보상"
    await interaction.followup.send(
        f"✅ `{section_name}` 에 `{항목이름}` 추가 완료\n" + "\n".join(lines), ephemeral=True,
    )

# ── /일괄추가 ──────────────────────────────────────────────
@tree.command(name="일괄추가", description="위플랩 룰렛 목록 복사 내용을 읽어 보상을 한 번에 추가합니다.")
@app_commands.describe(내용="위플랩 룰렛후원목록에서 복사한 텍스트를 그대로 붙여넣으세요.")
async def bulk_add_rewards(interaction: discord.Interaction, 내용: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    users = cfg.get("users", [])
    if not users:
        await interaction.followup.send("등록된 유저가 없습니다.", ephemeral=True)
        return

    users_by_name = sorted(users, key=lambda u: len(u.get("name", "")), reverse=True)
    added = {}
    record_starts = {"치즈", "구독"}
    blocks = []
    current_block = []

    for raw_line in 내용.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in record_starts and current_block:
            blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)

    for block in blocks:
        matched_user = next(
            (u for u in users_by_name if u.get("name") and any(line == u["name"] for line in block)),
            None,
        )
        if not matched_user:
            continue

        available_rewards = list(dict.fromkeys(
            matched_user.get("rewards", cfg.get("rewards", [])) +
            matched_user.get("sponsor_rewards", cfg.get("sponsor_rewards", []))
        ))
        for line in block:
            if not line.startswith("("):
                continue
            for reward in sorted(available_rewards, key=len, reverse=True):
                count = line.count(reward)
                if count <= 0:
                    continue
                matched_user.setdefault("counts", {})[reward] = matched_user.setdefault("counts", {}).get(reward, 0) + count
                added.setdefault(matched_user["name"], {})[reward] = added.setdefault(matched_user["name"], {}).get(reward, 0) + count

    if not added:
        await interaction.followup.send(
            "추가할 보상을 찾지 못했습니다.\n위플랩 표에서 이름과 룰렛 보상명이 같이 포함되도록 복사해서 붙여넣어 주세요.",
            ephemeral=True,
        )
        return

    save_config(cfg)
    lines = []
    for user_name, rewards in added.items():
        target = next(u for u in users if u["name"] == user_name)
        result = await update_post_message(target, cfg)
        reward_text = ", ".join(f"{name} +{count}" for name, count in rewards.items())
        lines.append(f"• {user_name}: {reward_text} — {result}")

    summary = "\n".join(lines)
    if len(summary) > 1800:
        summary = summary[:1800] + "\n...결과가 길어서 일부만 표시했습니다."
    await interaction.followup.send("**일괄 추가 완료**\n" + summary, ephemeral=True)

# ── /보상항목삭제 ──────────────────────────────────────────
@tree.command(name="보상항목삭제", description="보상 항목을 삭제하고 전체 유저 메시지를 업데이트합니다.")
@app_commands.describe(항목이름="삭제할 보상 항목 이름", 섹션="룰렛보상 또는 후원보상")
@app_commands.choices(섹션=[
    app_commands.Choice(name="룰렛보상", value="rewards"),
    app_commands.Choice(name="후원보상", value="sponsor_rewards"),
])
async def delete_reward(interaction: discord.Interaction, 항목이름: str, 섹션: str = "rewards"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    reward_list = cfg.get(섹션, [])
    if 항목이름 not in reward_list:
        await interaction.followup.send(f"항목 `{항목이름}` 을 찾을 수 없습니다.", ephemeral=True)
        return
    reward_list.remove(항목이름)
    cfg[섹션] = reward_list
    for u in cfg.get("users", []):
        u.get("counts", {}).pop(항목이름, None)
    save_config(cfg)
    lines = []
    for u in cfg.get("users", []):
        result = await update_post_message(u, cfg)
        lines.append(f"• {u.get('name', '?')} — {result}")
    section_name = "룰렛보상" if 섹션 == "rewards" else "후원보상"
    await interaction.followup.send(
        f"✅ `{section_name}` 에서 `{항목이름}` 삭제 완료\n" + "\n".join(lines), ephemeral=True,
    )

# ── /개별추가 ──────────────────────────────────────────────
@tree.command(name="개별추가", description="특정 유저에게만 보상 항목을 추가합니다.")
@app_commands.describe(닉네임="추가할 유저 닉네임", 항목이름="추가할 보상 항목 이름", 섹션="룰렛보상 또는 후원보상")
@app_commands.choices(섹션=[
    app_commands.Choice(name="룰렛보상", value="rewards"),
    app_commands.Choice(name="후원보상", value="sponsor_rewards"),
])
async def user_add_reward(interaction: discord.Interaction, 닉네임: str, 항목이름: str, 섹션: str = "rewards"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
    if not target:
        await interaction.followup.send(f"유저 `{닉네임}` 를 찾을 수 없습니다. `/유저목록`으로 등록된 이름을 확인해 주세요.", ephemeral=True)
        return
    if 섹션 not in target:
        target[섹션] = list(cfg.get(섹션, []))
    if 항목이름 in target[섹션]:
        await interaction.followup.send(f"이미 존재하는 항목입니다: `{항목이름}`", ephemeral=True)
        return
    target[섹션].append(항목이름)
    target.setdefault("counts", {})[항목이름] = 0
    save_config(cfg)
    result = await update_post_message(target, cfg)
    section_name = "룰렛보상" if 섹션 == "rewards" else "후원보상"
    await interaction.followup.send(
        f"✅ `{닉네임}` 의 `{section_name}` 에 `{항목이름}` 추가 완료\n포스트: {result}", ephemeral=True,
    )

# ── /개별삭제 ──────────────────────────────────────────────
@tree.command(name="개별삭제", description="특정 유저에게만 보상 항목을 삭제합니다.")
@app_commands.describe(닉네임="삭제할 유저 닉네임", 항목이름="삭제할 보상 항목 이름", 섹션="룰렛보상 또는 후원보상")
@app_commands.choices(섹션=[
    app_commands.Choice(name="룰렛보상", value="rewards"),
    app_commands.Choice(name="후원보상", value="sponsor_rewards"),
])
async def user_delete_reward(interaction: discord.Interaction, 닉네임: str, 항목이름: str, 섹션: str = "rewards"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
    if not target:
        await interaction.followup.send(f"유저 `{닉네임}` 를 찾을 수 없습니다. `/유저목록`으로 등록된 이름을 확인해 주세요.", ephemeral=True)
        return
    if 섹션 not in target:
        target[섹션] = list(cfg.get(섹션, []))
    if 항목이름 not in target[섹션]:
        await interaction.followup.send(f"항목 `{항목이름}` 을 찾을 수 없습니다.", ephemeral=True)
        return
    target[섹션].remove(항목이름)
    target.get("counts", {}).pop(항목이름, None)
    save_config(cfg)
    result = await update_post_message(target, cfg)
    section_name = "룰렛보상" if 섹션 == "rewards" else "후원보상"
    await interaction.followup.send(
        f"✅ `{닉네임}` 의 `{section_name}` 에서 `{항목이름}` 삭제 완료\n포스트: {result}", ephemeral=True,
    )

# ── 음악 컨트롤 버튼 View ────────────────────────────────
class MusicControlView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild

    @discord.ui.button(label="⏸️ 일시정지", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            button.label = "▶️ 재생"
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
        elif vc and vc.is_paused():
            vc.resume()
            button.label = "⏸️ 일시정지"
            button.style = discord.ButtonStyle.primary
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("❌ 재생 중인 음악이 없습니다.", ephemeral=True)

    @discord.ui.button(label="⏭️ 스킵", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ 스킵했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 재생 중인 음악이 없습니다.", ephemeral=True)

    @discord.ui.button(label="⏹️ 정지", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global is_playing
        vc = self.guild.voice_client
        if vc:
            music_queue.clear()
            is_playing = False
            await vc.disconnect()
            await interaction.response.send_message("⏹️ 정지하고 채널에서 나갔습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 음성 채널에 없습니다.", ephemeral=True)

    @discord.ui.button(label="📋 대기열", style=discord.ButtonStyle.secondary)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not music_queue:
            await interaction.response.send_message("📋 대기열이 비어있습니다.", ephemeral=True)
            return
        lines = [f"{i+1}. {title}" for i, (_, title, _) in enumerate(music_queue)]
        await interaction.response.send_message("📋 **대기열**\n" + "\n".join(lines), ephemeral=True)

# ── 음악 재생 내부 함수 ───────────────────────────────────
async def play_next(guild):
    global is_playing
    if not music_queue:
        is_playing = False
        return
    url, title, channel = music_queue.pop(0)
    vc = guild.voice_client
    if not vc:
        is_playing = False
        return
    def after_play(error):
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)
    vc.play(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS), after=after_play)
    is_playing = True
    embed = discord.Embed(
        description=f"🎵 **{title}**",
        color=0x1DB954,
    )
    embed.set_footer(text="⏸️ 일시정지  |  ⏭️ 스킵  |  ⏹️ 정지  |  📋 대기열")
    view = MusicControlView(guild)
    await channel.send(embed=embed, view=view)

# ── /뮤직 ─────────────────────────────────────────────────
@tree.command(name="뮤직", description="가수 - 제목 형식으로 입력하면 유튜브에서 자동 검색 후 재생합니다.")
@app_commands.describe(검색어="가수 - 제목 형식으로 입력 (예: 아이유 - 좋은날)")
async def play_music(interaction: discord.Interaction, 검색어: str):
    global is_playing
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 먼저 음성 채널에 입장해주세요.", ephemeral=True)
        return
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect()

    # "가수 - 제목" 형식이면 그대로, 아니면 그냥 검색어로 사용
    query = 검색어.strip()

    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        try:
            if "youtube.com" in query or "youtu.be" in query:
                info = ydl.extract_info(query, download=False)
            else:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)
                info = info['entries'][0]
            url = info['url']
            title = info.get('title', '알 수 없음')
            thumbnail = info.get('thumbnail', '')
            duration = info.get('duration', 0)
            mins, secs = divmod(duration, 60)
        except Exception as e:
            await interaction.followup.send(f"❌ 검색 실패: {e}")
            return

    music_queue.append((url, title, interaction.channel))

    if not is_playing:
        await play_next(interaction.guild)
        embed = discord.Embed(
            title="🎵 지금 재생 중",
            description=f"**{title}**",
            color=0x1DB954,
        )
        embed.add_field(name="검색어", value=query, inline=True)
        embed.add_field(name="길이", value=f"{mins}:{secs:02d}", inline=True)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        view = MusicControlView(interaction.guild)
        await interaction.followup.send(embed=embed, view=view)
    else:
        embed = discord.Embed(
            title="📋 대기열에 추가됨",
            description=f"**{title}**",
            color=0x5865F2,
        )
        embed.add_field(name="대기 순서", value=f"{len(music_queue)}번째", inline=True)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        await interaction.followup.send(embed=embed)

# ── /입장 ─────────────────────────────────────────────────
@tree.command(name="입장", description="봇이 음성 채널에 입장합니다.")
async def voice_join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 먼저 음성 채널에 입장해주세요.", ephemeral=True)
        return
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(interaction.user.voice.channel)
        await interaction.response.send_message(f"🔊 **{interaction.user.voice.channel.name}** 채널로 이동했습니다.")
    else:
        await interaction.user.voice.channel.connect()
        await interaction.response.send_message(f"🔊 **{interaction.user.voice.channel.name}** 채널에 입장했습니다.")

# ── /퇴장 ─────────────────────────────────────────────────
@tree.command(name="퇴장", description="봇이 음성 채널에서 퇴장합니다.")
async def voice_leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("👋 음성 채널에서 퇴장했습니다.")
    else:
        await interaction.response.send_message("❌ 봇이 음성 채널에 없습니다.", ephemeral=True)

# ── /TTS ──────────────────────────────────────────────────
@tree.command(name="tts", description="입력한 텍스트를 음성으로 읽어줍니다.")
@app_commands.describe(텍스트="읽어줄 텍스트", 언어="언어 선택 (기본: 한국어)")
@app_commands.choices(언어=[
    app_commands.Choice(name="한국어", value="ko"),
    app_commands.Choice(name="영어", value="en"),
    app_commands.Choice(name="일본어", value="ja"),
])
async def tts_play(interaction: discord.Interaction, 텍스트: str, 언어: str = "ko"):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 먼저 음성 채널에 입장해주세요.", ephemeral=True)
        return
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect()
    try:
        tts = gTTS(text=텍스트, lang=언어)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tts.save(tmp.name)
        tmp.close()
        def after_tts(error):
            os.unlink(tmp.name)
        vc.play(discord.FFmpegPCMAudio(tmp.name), after=after_tts)
        await interaction.followup.send(f"🔊 TTS 재생: **{텍스트}**")
    except Exception as e:
        await interaction.followup.send(f"❌ TTS 실패: {e}")

# ══════════════════════════════════════════════════════════
#  레벨 시스템 (leaderboard.run 호환)
#  - 공식: level N 달성에 필요한 XP = 5*N² + 50*N + 100 (Mee6/leaderboard.run 동일)
#  - 메시지당 XP: xp_min ~ xp_max 랜덤, cooldown_seconds 쿨다운
#  - 데이터: levels.json (CONFIG_PATH 와 같은 디렉터리)
# ══════════════════════════════════════════════════════════

_levels_lock = asyncio.Lock()

LEVEL_MILESTONE_REWARDS = {lv: lv // 10 for lv in range(10, 201, 10)}  # 10→1개, 20→2개, ..., 200→20개


def _default_levels() -> dict:
    return {
        "users": {},
        "settings": {
            "xp_min": 15,
            "xp_max": 25,
            "cooldown_seconds": 60,
            "excluded_channels": [],
            "level_up_channel": None,
            "announce": True,
            "blocked_guilds": [],
        },
    }


def _load_levels() -> dict:
    try:
        if os.path.exists(LEVELS_PATH):
            with open(LEVELS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            base = _default_levels()
            base["users"] = raw.get("users", {})
            base["settings"].update(raw.get("settings", {}))
            return base
    except Exception:
        pass
    return _default_levels()


def _save_levels(data: dict):
    os.makedirs(os.path.dirname(os.path.abspath(LEVELS_PATH)), exist_ok=True)
    tmp = LEVELS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LEVELS_PATH)


def _xp_for_level(level: int) -> int:
    """레벨 N → N+1 에 필요한 XP (leaderboard.run / Mee6 공식)"""
    return 5 * level * level + 50 * level + 100


def _total_xp_for_level(level: int) -> int:
    """레벨 N 시작 시점의 누적 XP"""
    return sum(_xp_for_level(i) for i in range(level))


def _calc_level(total_xp: int) -> tuple[int, int, int]:
    """(현재레벨, 현재레벨내XP, 다음레벨까지필요XP) 반환"""
    level, remaining = 0, total_xp
    while remaining >= _xp_for_level(level):
        remaining -= _xp_for_level(level)
        level += 1
    return level, remaining, _xp_for_level(level)


def _sorted_leaderboard(users: dict) -> list[tuple[str, dict]]:
    return sorted(users.items(), key=lambda x: x[1].get("xp", 0), reverse=True)


async def _grant_xp(message: discord.Message):
    """메시지 XP 지급 및 레벨업 처리"""
    if message.author.bot or not message.guild:
        return
    uid = str(message.author.id)
    now = time.time()

    new_level = old_level = 0
    announce = False
    level_up_channel_id = None

    async with _levels_lock:
        data = _load_levels()
        settings = data["settings"]

        blocked_guilds = settings.get("blocked_guilds") or []
        if message.guild.id in blocked_guilds:
            return

        excluded = settings.get("excluded_channels") or []
        if message.channel.id in excluded:
            return

        cooldown = settings.get("cooldown_seconds", 60)
        user = data["users"].setdefault(uid, {"xp": 0, "messages": 0, "last_xp_at": 0})

        if now - user.get("last_xp_at", 0) < cooldown:
            return

        old_level, _, _ = _calc_level(user.get("xp", 0))
        gained = random.randint(
            settings.get("xp_min", 15),
            max(settings.get("xp_min", 15), settings.get("xp_max", 25)),
        )
        user["xp"] = user.get("xp", 0) + gained
        user["messages"] = user.get("messages", 0) + 1
        user["last_xp_at"] = now
        new_level, _, _ = _calc_level(user["xp"])
        announce = settings.get("announce", True)
        level_up_channel_id = settings.get("level_up_channel")
        _save_levels(data)

    if new_level > old_level:
        target_ch = message.channel
        if level_up_channel_id:
            try:
                target_ch = message.guild.get_channel(int(level_up_channel_id)) or message.channel
            except Exception:
                pass

        if announce:
            embed = discord.Embed(
                description=f"🎉 {message.author.mention} 님이 **레벨 {new_level}** 에 도달했습니다!",
                color=0xFFD700,
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            try:
                await target_ch.send(embed=embed)
            except Exception:
                pass

        # 마일스톤 보상 체크 (announce 여부와 무관하게 항상 실행)
        for lv in range(old_level + 1, new_level + 1):
            reward_count = LEVEL_MILESTONE_REWARDS.get(lv)
            if reward_count:
                asyncio.create_task(_grant_milestone_reward(message.author, lv, reward_count, target_ch))


async def _grant_milestone_reward(member: discord.Member, level: int, reward_count: int, notify_ch):
    """레벨 마일스톤 달성 시 선참권 자동 지급"""
    reward_name = "선참권"
    try:
        # 인하우스 DB에서 discordId로 시청자 찾기
        db = await asyncio.to_thread(request_json, f"{INHOUSE_API_URL}/api/inhouse-db")
        viewer = next((v for v in db.get("viewers", []) if str(v.get("discordId", "")) == str(member.id)), None)
        if not viewer:
            return
        # 치지직 닉 or 롤닉으로 보관함봇 config 유저 매칭
        cfg = load_config()
        chzzk = (viewer.get("chzzk") or "").strip()
        name = (viewer.get("name") or "").strip()
        target = next((u for u in cfg.get("users", []) if u.get("name", "") in (chzzk, name)), None)
        if not target:
            return
        counts = target.setdefault("counts", {})
        before = counts.get(reward_name, 0)
        counts[reward_name] = before + reward_count
        save_config(cfg)
        await update_post_message(target, cfg)
        try:
            await notify_ch.send(
                f"🎁 **{member.display_name}** 님이 레벨 **{level}** 달성으로 "
                f"**{reward_name} {reward_count}개**를 획득했습니다! (누적 {counts[reward_name]}개)"
            )
        except Exception:
            pass
    except Exception:
        pass


# ── /랭크 ─────────────────────────────────────────────────
@tree.command(name="랭크", description="자신 또는 다른 유저의 레벨과 XP를 확인합니다.")
@app_commands.describe(유저="확인할 유저 (기본: 자신)")
async def rank_command(interaction: discord.Interaction, 유저: discord.Member = None):
    member = 유저 or interaction.user
    data = _load_levels()
    uid = str(member.id)
    user = data["users"].get(uid, {"xp": 0, "messages": 0})
    total_xp = user.get("xp", 0)
    level, cur_xp, need_xp = _calc_level(total_xp)

    lb = _sorted_leaderboard(data["users"])
    rank = next((i + 1 for i, (k, _) in enumerate(lb) if k == uid), len(lb) + 1)

    bar_len = 20
    filled = round(bar_len * cur_xp / need_xp) if need_xp else bar_len
    bar = "▓" * filled + "░" * (bar_len - filled)

    embed = discord.Embed(color=0x5865F2)
    embed.set_author(name=f"{member.display_name}의 레벨 카드", icon_url=member.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="레벨", value=f"**{level}**", inline=True)
    embed.add_field(name="서버 랭킹", value=f"**#{rank}**", inline=True)
    embed.add_field(name="총 XP", value=f"**{total_xp:,}** XP", inline=True)
    embed.add_field(
        name=f"진행도   `{cur_xp:,} / {need_xp:,} XP`",
        value=f"`{bar}`",
        inline=False,
    )
    embed.set_footer(text=f"총 메시지: {user.get('messages', 0):,}개")
    await interaction.response.send_message(embed=embed)


# ── /리더보드 ──────────────────────────────────────────────
@tree.command(name="리더보드", description="서버 레벨 상위 유저 목록을 확인합니다.")
async def leaderboard_command(interaction: discord.Interaction):
    await interaction.response.defer()
    data = _load_levels()
    lb = _sorted_leaderboard(data["users"])[:10]

    if not lb:
        await interaction.followup.send("아직 레벨 데이터가 없습니다.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, udata) in enumerate(lb):
        total_xp = udata.get("xp", 0)
        level, _, _ = _calc_level(total_xp)
        prefix = medals[i] if i < 3 else f"`{i + 1}.`"
        try:
            member = interaction.guild.get_member(int(uid)) or await interaction.guild.fetch_member(int(uid))
            name = member.display_name
        except Exception:
            name = f"알 수 없는 유저"
        lines.append(f"{prefix} **{name}** · Lv.{level} · {total_xp:,} XP")

    embed = discord.Embed(title="🏆 레벨 리더보드", description="\n".join(lines), color=0xFFD700)
    await interaction.followup.send(embed=embed)


# ── /레벨설정 ──────────────────────────────────────────────
@tree.command(name="레벨설정", description="(관리자) 메시지 XP 지급 설정을 변경합니다.")
@app_commands.describe(
    xp_min="메시지당 최소 XP (기본 15)",
    xp_max="메시지당 최대 XP (기본 25)",
    쿨다운="XP 쿨다운 초 (기본 60)",
    레벨업알림="레벨업 알림 ON/OFF",
)
async def level_settings(
    interaction: discord.Interaction,
    xp_min: int = None,
    xp_max: int = None,
    쿨다운: int = None,
    레벨업알림: bool = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    async with _levels_lock:
        data = _load_levels()
        s = data["settings"]
        if xp_min is not None:
            s["xp_min"] = max(1, xp_min)
        if xp_max is not None:
            s["xp_max"] = max(s.get("xp_min", 1), xp_max)
        if 쿨다운 is not None:
            s["cooldown_seconds"] = max(0, 쿨다운)
        if 레벨업알림 is not None:
            s["announce"] = 레벨업알림
        _save_levels(data)
    s = data["settings"]
    embed = discord.Embed(title="⚙️ 레벨 설정 저장 완료", color=0x5865F2)
    embed.add_field(name="XP 범위", value=f"{s['xp_min']} ~ {s['xp_max']}", inline=True)
    embed.add_field(name="쿨다운", value=f"{s['cooldown_seconds']}초", inline=True)
    embed.add_field(name="레벨업 알림", value="ON ✅" if s.get("announce", True) else "OFF ❌", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /레벨초기화 ────────────────────────────────────────────
@tree.command(name="레벨초기화", description="(관리자) 특정 유저의 XP와 레벨을 초기화합니다.")
@app_commands.describe(유저="초기화할 유저")
async def reset_level(interaction: discord.Interaction, 유저: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    async with _levels_lock:
        data = _load_levels()
        data["users"].pop(str(유저.id), None)
        _save_levels(data)
    await interaction.response.send_message(
        f"✅ `{유저.display_name}` 의 레벨 데이터를 초기화했습니다.", ephemeral=True
    )


# ── /레벨지정 ─────────────────────────────────────────────
@tree.command(name="레벨지정", description="(관리자) 유저 레벨을 직접 설정합니다. leaderboard.run 마이그레이션용.")
@app_commands.describe(유저="레벨을 설정할 유저", 레벨="설정할 레벨 번호")
async def set_level(interaction: discord.Interaction, 유저: discord.Member, 레벨: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    if not (0 <= 레벨 <= 500):
        await interaction.response.send_message("❌ 레벨은 0~500 사이여야 합니다.", ephemeral=True)
        return
    xp = _total_xp_for_level(레벨)
    async with _levels_lock:
        data = _load_levels()
        user = data["users"].setdefault(str(유저.id), {"xp": 0, "messages": 0, "last_xp_at": 0})
        user["xp"] = xp
        _save_levels(data)
    await interaction.response.send_message(
        f"✅ `{유저.display_name}` → **레벨 {레벨}** 설정 완료 (XP: {xp:,})", ephemeral=True
    )


# ── /레벨임포트 ────────────────────────────────────────────
class LevelImportModal(discord.ui.Modal, title="레벨 일괄 임포트"):
    data_input = discord.ui.TextInput(
        label="디스코드ID:레벨 (한 줄에 하나)",
        style=discord.TextStyle.long,
        placeholder="123456789012345678:10\n987654321098765432:7\n...\n\n디스코드 ID는 우클릭 → ID 복사",
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = [l.strip() for l in self.data_input.value.splitlines() if l.strip()]
        results = []
        async with _levels_lock:
            data = _load_levels()
            for line in lines:
                if ":" not in line:
                    results.append(f"❌ 형식 오류: `{line}`")
                    continue
                uid_str, lvl_str = line.rsplit(":", 1)
                uid_str = uid_str.strip().lstrip("<@!").rstrip(">")
                try:
                    uid = str(int(uid_str))
                    lvl = int(lvl_str.strip())
                    if not (0 <= lvl <= 500):
                        raise ValueError
                except ValueError:
                    results.append(f"❌ 파싱 오류: `{line}`")
                    continue
                xp = _total_xp_for_level(lvl)
                user = data["users"].setdefault(uid, {"xp": 0, "messages": 0, "last_xp_at": 0})
                user["xp"] = xp
                results.append(f"✅ `{uid}` → Lv.{lvl} ({xp:,} XP)")
            _save_levels(data)

        summary = "\n".join(results[:25])
        if len(results) > 25:
            summary += f"\n...외 {len(results) - 25}명"
        await interaction.followup.send(
            f"**임포트 완료 ({len([r for r in results if r.startswith('✅')])}명 성공)**\n{summary}",
            ephemeral=True,
        )


@tree.command(name="레벨임포트", description="(관리자) leaderboard.run 레벨 데이터를 일괄 임포트합니다.")
async def level_import(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.send_modal(LevelImportModal())


# ── /렙업정지 ─────────────────────────────────────────────
@tree.command(name="렙업정지", description="(관리자) 이 서버에서 XP 획득/레벨업을 정지하거나 재개합니다.")
async def level_block_guild(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    async with _levels_lock:
        data = _load_levels()
        blocked = data["settings"].setdefault("blocked_guilds", [])
        gid = interaction.guild_id
        if gid in blocked:
            blocked.remove(gid)
            status = "✅ 재개"
        else:
            blocked.append(gid)
            status = "🚫 정지"
        _save_levels(data)
    await interaction.response.send_message(
        f"{status} — 이 서버에서 XP 획득/레벨업이 **{'정지' if '정지' in status else '재개'}**됐습니다.",
        ephemeral=True,
    )


# ── 실행 ─────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN") or open("token.txt").read().strip()
bot.run(TOKEN)

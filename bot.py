import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
import yt_dlp
from gtts import gTTS
import tempfile

# ── config 로드 ──────────────────────────────────────────
CONFIG_PATH = "config.json"

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ── 봇 설정 ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

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
            pass

    try:
        sent = await thread.send(embed=embed)
        return f"신규전송 (메시지ID: {sent.id})"
    except Exception as e:
        return f"전송 실패: {e}"

# ── 이벤트 ───────────────────────────────────────────────
command_sync_done = False

@bot.event
async def on_ready():
    global command_sync_done
    if command_sync_done:
        print(f"봇 재연결: {bot.user} (명령어 동기화 건너뜀)")
        return
    guild = discord.Object(id=1428066375334756354)
    try:
        tree.clear_commands(guild=guild)
        deleted = await tree.sync(guild=guild)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        tree.clear_commands(guild=None)
        global_deleted = await tree.sync()
        print(f"봇 온라인: {bot.user} (ID: {bot.user.id})")
        print(f"기존 서버 명령어 삭제 완료: {len(deleted)}개")
        print(f"슬래시 명령어 강제 동기화 완료: {len(synced)}개")
        print(f"전역 명령어 삭제 요청 완료: {len(global_deleted)}개")
        command_sync_done = True
        for cmd in synced:
            print(f"  - /{cmd.name}")
    except Exception as e:
        print(f"명령어 동기화 실패: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

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

# ── /차감 ─────────────────────────────────────────────
@tree.command(name="차감", description="특정 유저의 보상 개수를 수정하고 포스트 메시지를 업데이트합니다.")
@app_commands.describe(닉네임="수정할 유저 닉네임", 보상이름="수정할 보상 항목 이름", 개수="변경할 개수")
async def edit_reward(interaction: discord.Interaction, 닉네임: str, 보상이름: str, 개수: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    target = next((u for u in cfg.get("users", []) if u["name"] == 닉네임), None)
    if not target:
        await interaction.followup.send(f"유저 `{닉네임}` 를 찾을 수 없습니다.", ephemeral=True)
        return
    all_rewards = (
        target.get("rewards", cfg.get("rewards", [])) +
        target.get("sponsor_rewards", cfg.get("sponsor_rewards", []))
    )
    if 보상이름 not in all_rewards:
        await interaction.followup.send(f"보상 항목 `{보상이름}` 이 존재하지 않습니다.", ephemeral=True)
        return
    target.setdefault("counts", {})[보상이름] = 개수
    save_config(cfg)
    result = await update_post_message(target, cfg)
    await interaction.followup.send(
        f"✅ `{닉네임}` 의 `{보상이름}` → **{개수}개** 로 변경\n포스트 메시지: {result}",
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
        await interaction.response.send_message(f"유저 `{닉네임}` 를 찾을 수 없습니다.", ephemeral=True)
        return
    embed = build_embed(target, cfg)
    await interaction.response.send_message(embed=embed, ephemeral=True)

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
        await interaction.response.send_message(f"이미 존재하는 유저입니다: `{닉네임}`", ephemeral=True)
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
@tree.command(name="유저삭제", description="유저를 config에서 삭제합니다.")
@app_commands.describe(닉네임="삭제할 유저 닉네임")
async def delete_user(interaction: discord.Interaction, 닉네임: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    cfg = load_config()
    users = cfg.get("users", [])
    target = next((u for u in users if u["name"] == 닉네임), None)
    if not target:
        await interaction.response.send_message(f"유저 `{닉네임}` 를 찾을 수 없습니다.", ephemeral=True)
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

# ── /추가 ──────────────────────────────────────────────
@tree.command(name="추가", description="룰렛보상 또는 후원보상 섹션에 항목을 추가합니다.")
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
        await interaction.followup.send(f"유저 `{닉네임}` 를 찾을 수 없습니다.", ephemeral=True)
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
        await interaction.followup.send(f"유저 `{닉네임}` 를 찾을 수 없습니다.", ephemeral=True)
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

# ── 실행 ─────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN") or open("token.txt").read().strip()
bot.run(TOKEN)

import discord
from discord.ext import commands
from discord import app_commands
import json
import os

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
bot = commands.Bot(command_prefix="!", intents=intents)

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
GUILD_ID = 1428066375334756354  # ← 본인 서버 ID

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    print(f"봇 온라인: {bot.user} (ID: {bot.user.id})")
    print(f"tree에 등록된 명령어 수: {len(bot.tree.get_commands())}")
    for cmd in bot.tree.get_commands():
        print(f"  - {cmd.name}")
    try:
        # 글로벌 명령어를 길드로 복사 → 즉시 반영
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"동기화 오류: {e}")

# ── 관리자 권한 체크 ──────────────────────────────────────
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

# ── /보상수정 ─────────────────────────────────────────────
@bot.tree.command(name="보상수정", description="특정 유저의 보상 개수를 수정하고 포스트 메시지를 업데이트합니다.")
@app_commands.describe(
    닉네임="수정할 유저 닉네임",
    보상이름="수정할 보상 항목 이름",
    개수="변경할 개수",
)
async def edit_reward(interaction: discord.Interaction, 닉네임: str, 보상이름: str, 개수: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    users = cfg.get("users", [])

    target = next((u for u in users if u["name"] == 닉네임), None)
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
@bot.tree.command(name="전체업데이트", description="모든 유저의 포스트 메시지를 현재 config 기준으로 일괄 업데이트합니다.")
async def update_all(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = load_config()
    users = cfg.get("users", [])

    if not users:
        await interaction.followup.send("유저가 없습니다. config.json 을 확인하세요.", ephemeral=True)
        return

    lines = []
    for u in users:
        result = await update_post_message(u, cfg)
        lines.append(f"• {u.get('name', '?')} — {result}")

    await interaction.followup.send("**전체 업데이트 완료**\n" + "\n".join(lines), ephemeral=True)

# ── /보상현황 ──────────────────────────────────────────────
@bot.tree.command(name="보상현황", description="특정 유저의 현재 보상 현황을 확인합니다.")
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
@bot.tree.command(name="유저추가", description="새 유저를 config에 추가합니다.")
@app_commands.describe(
    닉네임="추가할 유저 닉네임",
    스레드id="해당 유저 포스트(스레드) ID",
    메시지id="기존 메시지 ID (없으면 비워두세요)",
)
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

# ── /자동등록 ──────────────────────────────────────────────
FORUM_CHANNEL_ID = 1491054022877253642

@bot.tree.command(name="자동등록", description="포스트 채널의 스레드를 자동 스캔해서 유저 목록을 등록합니다.")
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
            if str(t.id) not in [th.id for th in threads]:
                threads.append(t)
    except Exception:
        pass

    for thread in threads:
        tid = str(thread.id)
        if tid in existing_ids:
            continue
        new_user = {
            "name": thread.name,
            "thread_id": tid,
            "message_id": "",
            "counts": {r: 0 for r in rewards},
        }
        cfg.setdefault("users", []).append(new_user)
        new_users.append(thread.name)

    save_config(cfg)

    if not new_users:
        await interaction.followup.send("새로 등록할 스레드가 없습니다. (이미 모두 등록됨)", ephemeral=True)
        return

    await interaction.followup.send(
        f"✅ **{len(new_users)}개 스레드 자동 등록 완료!**\n" +
        "\n".join(f"• {n}" for n in new_users) +
        "\n\n이제 `/전체업데이트` 를 실행하면 각 포스트에 보상 메시지가 전송됩니다.",
        ephemeral=True,
    )

# ── /보상항목삭제 ──────────────────────────────────────────
@bot.tree.command(name="보상항목삭제", description="보상 항목을 삭제하고 전체 유저 메시지를 업데이트합니다.")
@app_commands.describe(
    항목이름="삭제할 보상 항목 이름",
    섹션="룰렛보상 또는 후원보상",
)
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
        f"✅ `{section_name}` 에서 `{항목이름}` 삭제 완료\n**전체 메시지 업데이트**\n" + "\n".join(lines),
        ephemeral=True,
    )

# ── /보상추가 ───────────────────────────────────────────
@bot.tree.command(name="보상추가", description="룰렛보상 또는 후원보상 섹션에 항목을 추가합니다.")
@app_commands.describe(
    항목이름="추가할 보상 항목 이름",
    섹션="룰렛보상 또는 후원보상",
)
@app_commands.choices(섹션=[
    app_commands.Choice(name="룰렛보상", value="rewards"),
    app_commands.Choice(name="후원보상", value="sponsor_rewards"),
])
async def add_reward_v2(interaction: discord.Interaction, 항목이름: str, 섹션: str = "rewards"):
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
        f"✅ `{section_name}` 에 `{항목이름}` 추가 완료\n**전체 메시지 업데이트**\n" + "\n".join(lines),
        ephemeral=True,
    )

# ── /개별추가 ──────────────────────────────────────────────
@bot.tree.command(name="개별추가", description="특정 유저에게만 보상 항목을 추가합니다.")
@app_commands.describe(
    닉네임="추가할 유저 닉네임",
    항목이름="추가할 보상 항목 이름",
    섹션="룰렛보상 또는 후원보상",
)
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
        f"✅ `{닉네임}` 의 `{section_name}` 에 `{항목이름}` 추가 완료\n포스트: {result}",
        ephemeral=True,
    )

# ── /개별삭제 ──────────────────────────────────────────────
@bot.tree.command(name="개별삭제", description="특정 유저에게만 보상 항목을 삭제합니다.")
@app_commands.describe(
    닉네임="삭제할 유저 닉네임",
    항목이름="삭제할 보상 항목 이름",
    섹션="룰렛보상 또는 후원보상",
)
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
        f"✅ `{닉네임}` 의 `{section_name}` 에서 `{항목이름}` 삭제 완료\n포스트: {result}",
        ephemeral=True,
    )

# ── 실행 ─────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN") or open("token.txt").read().strip()
bot.run(TOKEN)

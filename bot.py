"""
INSIDEX Bot — ReShade Edition
- ซื้อแล้วได้ยศ Reshade ทันที
- จากนั้นเลือกยศ down- เสริม 1 ตัว
- ราคา 39.- รวมทุกอย่าง
- EasySlip API: ตรวจยอด + ชื่อผู้รับ + เวลา ≤30 นาที
- ป้องกันสลิปซ้ำ SHA-256
- Private Thread ต่อ 1 ลูกค้า — ไม่เห็นแชทของคนอื่น
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────
TOKEN            = os.getenv("DISCORD_TOKEN")
GUILD_ID         = int(os.getenv("GUILD_ID", "0"))
ADMIN_ROLE_ID    = int(os.getenv("ADMIN_ROLE_ID", "0"))
LOG_CHANNEL_ID   = int(os.getenv("LOG_CHANNEL_ID", "0"))
EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY")

BANK_NAME     = os.getenv("BANK_NAME", "กสิกรไทย (KBank)")
BANK_ACC_NAME = os.getenv("BANK_ACCOUNT_NAME", "INSIDEX SHOP")
BANK_ACC_NO   = os.getenv("BANK_ACCOUNT_NUMBER", "XXX-X-XXXXX-X")
TRUE_NUMBER   = os.getenv("TRUEMONEY_NUMBER", "0XX-XXX-XXXX")

PRICE             = int(os.getenv("RESHADE_PRICE", "39"))
PAYMENT_IMAGE_URL = "https://media.discordapp.net/attachments/1446487555091730544/1496205096734949516/39.png?ex=69f58f55&is=69f43dd5&hm=a06185f0dc2fee0564e92d3093ffa03f4fe47e23dd65c451e794cd416853c891&format=webp&quality=lossless&width=1037&height=1037&"
SHOP_BANNER_URL   = "https://cdn.discordapp.com/attachments/1446487555091730544/1499837254078697643/21.png?ex=69f63fcb&is=69f4ee4b&hm=03af46f901158128aa3e5758cca55bdd53059f754d9b5b834b2ba77f3503830f&"
TH     = timezone(timedelta(hours=7))
PURPLE = 0x7b2cbf

ACCEPTED_RECEIVERS = ["SIRIPOOM INTAPANYA", "SIRIPHOOM INTAPANYA", "สิริภูมิ อินตะปัญญา"]

# ─────────────────────────────────────────
#  ROLES
# ─────────────────────────────────────────
ROLE_RESHADE_ID = int(os.getenv("ROLE_RESHADE", "0"))

DOWN_ROLES = [
    {"label": "Moretime",   "env": "ROLE_DOWN_MORETIME"},
    {"label": "Dotashd.v1", "env": "ROLE_DOWN_DOTASHD_V1"},
    {"label": "Dotashd.v2", "env": "ROLE_DOWN_DOTASHD_V2"},
    {"label": "Dotashd.wf", "env": "ROLE_DOWN_DOTASHD_WF"},
    {"label": "Dotashd.v3", "env": "ROLE_DOWN_DOTASHD_V3"},
    {"label": "Dotasuns",   "env": "ROLE_DOWN_DOTASUNS"},
    {"label": "Dotashd.bw", "env": "ROLE_DOWN_DOTASHD_BW"},
    {"label": "Doinluv.01", "env": "ROLE_DOWN_DOINLUV_01"},
    {"label": "Doinluv.02", "env": "ROLE_DOWN_DOINLUV_02"},
    {"label": "Doinluv.03", "env": "ROLE_DOWN_DOINLUV_03"},
    {"label": "Doinluv.04", "env": "ROLE_DOWN_DOINLUV_04"},
]

def get_down_role_id(env_key: str) -> int:
    return int(os.getenv(env_key, "0"))

# ─────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────
pending_orders:   dict = {}
used_slip_hashes: set  = set()
shop_embed_ids:   dict = {}
user_threads:     dict = {}

STATE_FILE = "state.json"

def load_state():
    global pending_orders, used_slip_hashes, user_threads
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            pending_orders   = data.get("pending_orders", {})
            used_slip_hashes = set(data.get("used_slip_hashes", []))
            user_threads     = {int(k): v for k, v in data.get("user_threads", {}).items()}

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump({
            "pending_orders":   pending_orders,
            "used_slip_hashes": list(used_slip_hashes),
            "user_threads":     user_threads,
        }, f)


# ─────────────────────────────────────────
#  EASYSLIP VERIFY
# ─────────────────────────────────────────
def _extract_receiver(payload: dict) -> str:
    """
    รองรับ 3 รูปแบบ:
    1. Bank slip  → receiver.bank.account.name.th / .en
    2. TrueMoney  → receiver.name (string ตรงๆ)
    3. PromptPay  → receiver.account.name.th / .en
    """
    rec = payload.get("receiver", {})

    # รูปแบบ 1: bank
    bank_name = rec.get("bank", {}).get("account", {}).get("name", {})
    if isinstance(bank_name, dict) and (bank_name.get("th") or bank_name.get("en")):
        th = bank_name.get("th", "") or ""
        en = bank_name.get("en", "") or ""
        return f"{th} {en}".strip()

    # รูปแบบ 2: truemoney (receiver.name เป็น string)
    name_str = rec.get("name")
    if isinstance(name_str, str) and name_str:
        return name_str.strip()

    # รูปแบบ 3: promptpay / account.name
    acc_name = rec.get("account", {}).get("name", {})
    if isinstance(acc_name, dict):
        th = acc_name.get("th", "") or ""
        en = acc_name.get("en", "") or ""
        return f"{th} {en}".strip()

    return ""


async def ocr_slip(image_url: str) -> dict:
    # ดาวน์โหลดรูป
    async with aiohttp.ClientSession() as s:
        async with s.get(image_url) as r:
            if r.status != 200:
                return {"ok": False, "reason": "ดาวน์โหลดรูปไม่สำเร็จ"}
            img_bytes    = await r.read()
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]

    # เช็คสลิปซ้ำ
    slip_hash = hashlib.sha256(img_bytes).hexdigest()
    if slip_hash in used_slip_hashes:
        return {"ok": False, "reason": "❌ สลิปนี้ถูกใช้ไปแล้ว"}

    now_th = datetime.now(TH)

    # ส่ง EasySlip API
    form = aiohttp.FormData()
    form.add_field("file", img_bytes, filename="slip.jpg", content_type=content_type)

    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://developer.easyslip.com/api/v1/verify",
            headers={"Authorization": f"Bearer {EASYSLIP_API_KEY}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status != 200:
                return {"ok": False, "reason": f"EasySlip error {r.status}"}
            data = await r.json()

    if data.get("status") != 200:
        reason = data.get("message", "ไม่ทราบสาเหตุ")
        return {"ok": False, "reason": f"❌ ตรวจสลิปไม่ได้: {reason}"}

    payload = data.get("data", {})

    # เช็คยอด
    amount_obj = payload.get("amount", {})
    # bank slip → amount.amount | truemoney → amount (ตัวเลขตรง)
    if isinstance(amount_obj, dict):
        amt = float(amount_obj.get("amount", 0))
    else:
        amt = float(amount_obj or 0)

    if round(amt) != PRICE:
        return {"ok": False, "reason": f"❌ ยอดไม่ตรง (พบ ฿{amt:.0f} ต้อง ฿{PRICE})"}

    # เช็คผู้รับ
    receiver_full = _extract_receiver(payload)
    if not any(name.lower() in receiver_full.lower() for name in ACCEPTED_RECEIVERS):
        return {"ok": False, "reason": f"❌ ชื่อผู้รับไม่ตรง (พบ: {receiver_full or 'ไม่มี'})"}

    # เช็คเวลา
    dt_str = payload.get("date", "")
    if dt_str:
        try:
            slip_dt = datetime.fromisoformat(dt_str).astimezone(TH)
            if (now_th - slip_dt).total_seconds() > 1800:
                return {"ok": False, "reason": f"❌ สลิปเกิน 30 นาที (เวลาสลิป: {slip_dt.strftime('%H:%M')})"}
        except Exception:
            pass

    used_slip_hashes.add(slip_hash)
    return {
        "ok":        True,
        "amount":    amt,
        "receiver":  receiver_full,
        "slip_time": dt_str,
        "slip_type": "bank",
    }


# ─────────────────────────────────────────
#  BOT
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ─────────────────────────────────────────
#  VIEW: เลือกยศ down-
# ─────────────────────────────────────────
class DownRoleSelect(discord.ui.Select):
    def __init__(self, order_id: str, thread_id: int):
        self.order_id  = order_id
        self.thread_id = thread_id
        options = [
            discord.SelectOption(label=r["label"], value=r["env"])
            for r in DOWN_ROLES
        ]
        super().__init__(
            placeholder="🎮 เลือก Reshade ที่ต้องการ...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="select_down_role",
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = [(r["label"], r["env"]) for r in DOWN_ROLES if r["env"] in self.values]
        roles_to_add = []
        for label, env in chosen:
            role = interaction.guild.get_role(get_down_role_id(env))
            if role:
                roles_to_add.append(role)

        if roles_to_add:
            await interaction.user.add_roles(*roles_to_add, reason=f"INSIDEX {self.order_id}")

        chosen_labels = ", ".join(f"`{l}`" for l, _ in chosen)

        DOWN_ROLE_CHANNELS = {
            "Moretime":   int(os.getenv("CH_DOWN_MORETIME",   "0")),
            "Dotashd.v1": int(os.getenv("CH_DOWN_DOTASHD_V1", "0")),
            "Dotashd.v2": int(os.getenv("CH_DOWN_DOTASHD_V2", "0")),
            "Dotashd.wf": int(os.getenv("CH_DOWN_DOTASHD_WF", "0")),
            "Dotashd.v3": int(os.getenv("CH_DOWN_DOTASHD_V3", "0")),
            "Dotasuns":   int(os.getenv("CH_DOWN_DOTASUNS",   "0")),
            "Dotashd.bw": int(os.getenv("CH_DOWN_DOTASHD_BW", "0")),
            "Doinluv.01": int(os.getenv("CH_DOWN_DOINLUV_01", "0")),
            "Doinluv.02": int(os.getenv("CH_DOWN_DOINLUV_02", "0")),
            "Doinluv.03": int(os.getenv("CH_DOWN_DOINLUV_03", "0")),
            "Doinluv.04": int(os.getenv("CH_DOWN_DOINLUV_04", "0")),
        }

        try:
            dm_lines = []
            for label, _ in chosen:
                ch_id = DOWN_ROLE_CHANNELS.get(label, 0)
                if ch_id:
                    dm_lines.append(f"🎮 **{label}** → <#{ch_id}>")
                else:
                    dm_lines.append(f"🎮 **{label}** → (ไม่ได้ตั้งค่าห้อง)")

            await interaction.user.send(embed=discord.Embed(
                title="🎮 ยศ Reshade ของคุณพร้อมแล้ว!",
                description=(
                    f"**Order ID:** `{self.order_id}`\n\n"
                    f"คุณสามารถเข้าห้องด้านล่างได้เลยครับ\n\n"
                    + "\n".join(dm_lines) +
                    "\n\nขอบคุณที่ใช้บริการ **INSIDEX** 🙏"
                ),
                color=PURPLE,
            ))
        except Exception:
            pass

        log_ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_ch:
            await log_ch.send(embed=discord.Embed(
                title="🎮 Down Role Selected",
                description=(
                    f"**User:** {interaction.user.mention} ({interaction.user.name})\n"
                    f"**Order ID:** `{self.order_id}`\n"
                    f"**ยศที่เลือก:** {chosen_labels}"
                ),
                color=PURPLE,
                timestamp=datetime.now(),
            ))

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="<a:1134verifiedanimated:1495470992452227103> เสร็จสมบูรณ์!",
                description=(
                    f"**ได้รับยศแล้ว :**\n"
                    f"🎨 **Reshade** + {chosen_labels}\n\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "ขอบคุณที่ใช้บริการ **INSIDEX** 🙏\n"
                    "> ห้องนี้จะถูกลบใน 5 วินาที"
                ),
                color=PURPLE,
            ),
            view=None,
        )

        await asyncio.sleep(5)
        thread = interaction.guild.get_thread(self.thread_id)
        if thread:
            try:
                await thread.delete()
            except Exception:
                pass

        user_threads.pop(interaction.user.id, None)
        save_state()


class DownRoleView(discord.ui.View):
    def __init__(self, order_id: str, thread_id: int):
        super().__init__(timeout=None)
        self.add_item(DownRoleSelect(order_id, thread_id))


# ─────────────────────────────────────────
#  PAYMENT VIEWS
# ─────────────────────────────────────────
class PaymentView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=None)
        self.order_id = order_id

    def _order(self):
        return pending_orders.get(self.order_id)

    @discord.ui.button(label="🏦 Bank", style=discord.ButtonStyle.primary, custom_id="payment_bank")
    async def bank(self, interaction: discord.Interaction, _: discord.ui.Button):
        o = self._order()
        if not o:
            return await interaction.response.send_message("❌ Order หมดอายุ", ephemeral=True)
        o["payment_method"] = "bank"
        o["status"]         = "waiting_slip"
        embed = discord.Embed(
            title="🏦 โอนผ่านธนาคาร / PromptPay",
            description=(
                f"**สินค้า :** 🎨 ReShade\n"
                f"**ยอด : ฿{PRICE}**\n\n"
                f"```\nธนาคาร : {BANK_NAME}\n"
                f"ชื่อบัญชี : {BANK_ACC_NAME}\n"
                f"เลขบัญชี : {BANK_ACC_NO}\n```\n"
                f"🔖 Order ID : `{self.order_id}`\n\n"
                "📸 **ส่งรูปสลิปในห้องนี้ได้เลย**\n"
                "ระบบตรวจอัตโนมัติ ~10 วินาที"
            ),
            color=PURPLE,
        )
        embed.set_image(url=PAYMENT_IMAGE_URL)
        await interaction.response.edit_message(embed=embed, view=CancelView(self.order_id))

    @discord.ui.button(label="💰 TrueMoney Wallet", style=discord.ButtonStyle.success, custom_id="payment_truemoney")
    async def truemoney(self, interaction: discord.Interaction, _: discord.ui.Button):
        o = self._order()
        if not o:
            return await interaction.response.send_message("❌ Order หมดอายุ", ephemeral=True)
        o["payment_method"] = "truemoney"
        o["status"]         = "waiting_slip"
        embed = discord.Embed(
            title="💰 โอนผ่าน TrueMoney Wallet",
            description=(
                f"🎨 **ReShade Pack**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 **ยอดชำระ : ฿{PRICE}**\n\n"
                f"```\nเบอร์รับเงิน : {TRUE_NUMBER}\n```\n"
                f"🔖 **Order ID :** `{self.order_id}`\n\n"
                "📸 **ส่งรูปสลิปในห้องนี้ได้เลย**\n"
                "> ระบบตรวจอัตโนมัติ ~10 วินาที"
            ),
            color=PURPLE,
        )
        embed.set_image(url=PAYMENT_IMAGE_URL)
        await interaction.response.edit_message(embed=embed, view=CancelView(self.order_id))


class CancelView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="❌ ยกเลิก Order", style=discord.ButtonStyle.danger, custom_id="cancel_order")
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        pending_orders.pop(self.order_id, None)
        user_threads.pop(interaction.user.id, None)
        save_state()
        await interaction.response.edit_message(
            content="❌ ยกเลิก Order แล้ว ห้องนี้จะถูกลบใน 5 วินาที",
            embed=None, view=None
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception:
            pass


class ShopEmbedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎨 ซื้อ ReShade — ฿39",
        style=discord.ButtonStyle.primary,
        custom_id="insidex_buy_reshade",
    )
    async def buy(self, interaction: discord.Interaction, _: discord.ui.Button):
        await _start_order(interaction)


# ─────────────────────────────────────────
#  ORDER START
# ─────────────────────────────────────────
async def _start_order(interaction: discord.Interaction):
    member  = interaction.user
    channel = interaction.channel

    existing_thread_id = user_threads.get(member.id)
    if existing_thread_id:
        existing = interaction.guild.get_thread(existing_thread_id)
        if existing:
            return await interaction.response.send_message(
                f"❗ คุณมี order ค้างอยู่แล้ว → {existing.mention}",
                ephemeral=True
            )
        else:
            user_threads.pop(member.id, None)
            for oid in [o for o, v in pending_orders.items() if v["user_id"] == member.id]:
                pending_orders.pop(oid, None)

    order_id = str(uuid.uuid4())[:8].upper()

    try:
        thread = await channel.create_thread(
            name=f"🛒 {member.display_name} · {order_id}",
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=60,
        )
        await thread.add_user(member)
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ บอทไม่มีสิทธิ์สร้าง Thread กรุณาแจ้งแอดมิน", ephemeral=True
        )

    user_threads[member.id] = thread.id
    pending_orders[order_id] = {
        "user_id":        member.id,
        "user_name":      member.name,
        "thread_id":      thread.id,
        "status":         "pending_payment",
        "payment_method": None,
        "timestamp":      datetime.now(TH).isoformat(),
    }
    save_state()

    embed = discord.Embed(
        title="🛒 สั่งซื้อ ReShade",
        description=(
            f"สวัสดี {member.mention}!\n\n"
            f"**ราคา :** ฿{PRICE}\n"
            f"**Order ID :** `{order_id}`\n\n"
            "ของที่จะได้รับ :\n"
            "<a:1134verifiedanimated:1495470992452227103> ยศ **Reshade** ทันที\n"
            "🎮 เลือกยศ **Reshade** ได้ 1 ตัว\n\n"
            "เลือกวิธีชำระด้านล่าง"
        ),
        color=PURPLE,
    )
    await thread.send(embed=embed, view=PaymentView(order_id))
    await interaction.response.send_message(
        f"<a:1134verifiedanimated:1495470992452227103> สร้างห้องส่วนตัวให้แล้ว กดที่นี่ → {thread.mention}",
        ephemeral=True
    )


# ─────────────────────────────────────────
#  GRANT
# ─────────────────────────────────────────
async def grant_reshade_and_pick(thread, guild, member, order_id, ocr, method):
    reshade_role = guild.get_role(ROLE_RESHADE_ID)
    if reshade_role:
        await member.add_roles(reshade_role, reason=f"INSIDEX {order_id}")

    try:
        await member.send(embed=discord.Embed(
            title="<a:1134verifiedanimated:1495470992452227103> ได้รับยศ Reshade แล้ว!",
            description=(
                f"**Order ID:** `{order_id}`\n\n"
                "<a:1134verifiedanimated:1495470992452227103> ยศ **Reshade** ถูกมอบให้แล้ว!\n\n"
                "> กลับไปที่ห้องส่วนตัวแล้วเลือกยศ **Reshade ที่ต้องการได้เลย**"
            ),
            color=PURPLE,
        ))
    except Exception:
        pass

    log_ch = guild.get_channel(LOG_CHANNEL_ID)
    if log_ch:
        e = discord.Embed(title="💳 Purchase — ReShade", color=PURPLE, timestamp=datetime.now())
        e.add_field(name="User",      value=f"{member.mention} ({member.name})", inline=True)
        e.add_field(name="ยอด",       value=f"฿{ocr['amount']:.0f}",            inline=True)
        e.add_field(name="วิธีชำระ", value=method,                              inline=True)
        e.add_field(name="Order ID",  value=f"`{order_id}`",                    inline=True)
        e.add_field(name="ผู้รับ",    value=ocr["receiver"] or "-",             inline=True)
        e.add_field(name="เวลาสลิป", value=ocr.get("slip_time") or "-",        inline=True)
        await log_ch.send(embed=e)

    await thread.send(
        content=member.mention,
        embed=discord.Embed(
            title="🎮 เลือกยศ Reshade",
            description=(
                "ยศ **Reshade** ถูกมอบให้แล้ว <a:1134verifiedanimated:1495470992452227103>\n\n"
                "เลือกยศ **Reshade** ที่ต้องการ 1 ตัว\n"
                "*(รวมในราคา ฿39 แล้ว)*\n\n"
                "⚠️ หลังเลือกแล้ว ห้องนี้จะถูกลบอัตโนมัติใน 5 วินาที"
            ),
            color=PURPLE,
        ),
        view=DownRoleView(order_id, thread.id),
    )


# ─────────────────────────────────────────
#  CLEANUP TASK
# ─────────────────────────────────────────
async def cleanup_expired_orders():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(TH)
        expired = [
            oid for oid, o in pending_orders.items()
            if (now - datetime.fromisoformat(o["timestamp"])).total_seconds() > 3600
        ]
        for oid in expired:
            o = pending_orders.pop(oid, None)
            if o:
                user_threads.pop(o["user_id"], None)
                guild = bot.get_guild(GUILD_ID)
                if guild:
                    thread = guild.get_thread(o["thread_id"])
                    if thread:
                        try:
                            await thread.delete()
                        except Exception:
                            pass
        if expired:
            save_state()
        await asyncio.sleep(300)


# ─────────────────────────────────────────
#  EVENTS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    load_state()
    print(f"✅ INSIDEX Bot: {bot.user}")
    bot.add_view(ShopEmbedView())
    bot.add_view(PaymentView(""))
    bot.add_view(CancelView(""))
    bot.add_view(DownRoleView("", 0))
    asyncio.ensure_future(cleanup_expired_orders())
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if isinstance(message.channel, discord.Thread) and message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image/"):
            entry = next(
                ((oid, o) for oid, o in pending_orders.items()
                 if o["user_id"] == message.author.id
                 and o.get("thread_id") == message.channel.id
                 and o["status"] == "waiting_slip"),
                None,
            )
            if entry:
                order_id, order = entry
                order["status"] = "verifying"

                checking_msg = await message.reply(embed=discord.Embed(
                    title="🔍 กำลังตรวจสอบสลิป...",
                    description="> กำลังอ่านข้อมูล\n> กรุณารอสักครู่ (~10 วินาที)",
                    color=PURPLE,
                ))

                ocr = await ocr_slip(att.url)

                if ocr["ok"]:
                    order["status"] = "completed"
                    pending_orders.pop(order_id, None)
                    save_state()
                    await checking_msg.edit(embed=discord.Embed(
                        title="<a:1134verifiedanimated:1495470992452227103> สลิปผ่าน! กำลังมอบยศ...",
                        description=(
                            f"**ยอด :** ฿{ocr['amount']:.0f}\n"
                            f"**ผู้รับ :** {ocr['receiver'] or '-'}\n"
                            f"**เวลาสลิป :** {ocr.get('slip_time') or '-'}"
                        ),
                        color=PURPLE,
                    ))
                    await grant_reshade_and_pick(
                        thread=message.channel,
                        guild=message.guild,
                        member=message.author,
                        order_id=order_id,
                        ocr=ocr,
                        method=order["payment_method"],
                    )
                else:
                    order["status"] = "waiting_slip"
                    await checking_msg.edit(embed=discord.Embed(
                        title="❌ ตรวจสลิปไม่ผ่าน",
                        description=f"{ocr['reason']}\n\nกรุณาส่งสลิปใหม่ หรือติดต่อแอดมิน",
                        color=0xe74c3c,
                    ))

    await bot.process_commands(message)


# ─────────────────────────────────────────
#  SLASH COMMANDS (Admin)
# ─────────────────────────────────────────
@bot.tree.command(name="setup_shop", description="[Admin] วาง shop embed ถาวรในห้อง")
async def setup_shop(interaction: discord.Interaction):
    if not any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles):
        return await interaction.response.send_message("❌ ไม่มีสิทธิ์", ephemeral=True)
    embed = discord.Embed(
        title="🎨 RESHADE AUTO BUY",
        description=(
            "**:shopping_cart: บริการจำหน่าย Reshade อัตโนมัติ**\n"
            "ถ้าต้องการบริการลง Reshade สามารถกด https://discord.com/channels/1400021255528382526/1432715699138072699 มาได้เลยนะครับ ค่าบริการ **15.-**\n\n"
            f"**ราคา : ฿{PRICE}**\n\n"
            "ซื้อแล้วได้ :\n"
            "<a:1134verifiedanimated:1495470992452227103> ได้ยศ **Reshade** ทันที\n"
            "🎮 เลือกยศ **Reshade** ที่ต้องการ 1 ตัว\n\n"
            "💳 รับชำระ : ธนาคาร / TrueMoney\n"
            "<a:2902originallyknownas:1495471157862989964> ตรวจสลิปอัตโนมัติ — รับยศทันที!"
        ),
        color=PURPLE,
    )
    embed.set_footer(text="INSIDEX | BUY AUTO ✨")
    embed.set_image(url=SHOP_BANNER_URL)
    shop_msg = await interaction.channel.send(embed=embed, view=ShopEmbedView())
    shop_embed_ids[interaction.channel_id] = shop_msg.id
    await interaction.response.send_message("<a:1134verifiedanimated:1495470992452227103> วาง shop embed แล้ว", ephemeral=True)


@bot.tree.command(name="give_reshade", description="[Admin] มอบ Reshade + down- ให้ user")
@app_commands.describe(member="user ที่จะให้", down_role="ยศ down- ที่จะมอบ")
@app_commands.choices(down_role=[
    app_commands.Choice(name=r["label"], value=r["env"]) for r in DOWN_ROLES
])
async def give_reshade(interaction: discord.Interaction, member: discord.Member, down_role: str):
    if not any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles):
        return await interaction.response.send_message("❌ ไม่มีสิทธิ์", ephemeral=True)
    guild        = interaction.guild
    reshade      = guild.get_role(ROLE_RESHADE_ID)
    down         = guild.get_role(get_down_role_id(down_role))
    chosen_label = next(r["label"] for r in DOWN_ROLES if r["env"] == down_role)
    roles_to_add = [r for r in [reshade, down] if r]
    if roles_to_add:
        await member.add_roles(*roles_to_add)
    await interaction.response.send_message(
        f"<a:1134verifiedanimated:1495470992452227103> มอบ **Reshade** + **{chosen_label}** ให้ {member.mention} แล้ว",
        ephemeral=True
    )
    try:
        await member.send(embed=discord.Embed(
            title="🎁 ได้รับสินค้าจากแอดมิน",
            description=f"ได้รับยศ **Reshade** + **{chosen_label}** แล้ว!\nขอบคุณที่ใช้บริการ INSIDEX 🙏",
            color=PURPLE,
        ))
    except Exception:
        pass


@bot.tree.command(name="orders", description="[Admin] ดู pending orders")
async def orders_cmd(interaction: discord.Interaction):
    if not any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles):
        return await interaction.response.send_message("❌ ไม่มีสิทธิ์", ephemeral=True)
    if not pending_orders:
        return await interaction.response.send_message("📭 ไม่มี pending orders", ephemeral=True)
    embed = discord.Embed(title="📋 Pending Orders", color=PURPLE)
    for oid, o in list(pending_orders.items())[:10]:
        embed.add_field(
            name=f"`{oid}`",
            value=f"<@{o['user_id']}> | {o['status']} | {o.get('payment_method') or 'ยังไม่เลือก'}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
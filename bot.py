"""
INSIDEX Bot — ReShade Edition
- เลือก Reshade ได้หลายตัว ราคาตัวละ ฿39
- Bank: EasySlip API v1
- TrueMoney: Claude Vision OCR
- QR PromptPay ล็อกยอดอัตโนมัติตาม order (เฉพาะ Bank)
- ป้องกันสลิปซ้ำ SHA-256
- Private Thread ต่อ 1 ลูกค้า
- Restore order_id จาก embed footer หลัง bot restart
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO

from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────
TOKEN             = os.getenv("DISCORD_TOKEN")
GUILD_ID          = int(os.getenv("GUILD_ID", "0"))
ADMIN_ROLE_ID     = int(os.getenv("ADMIN_ROLE_ID", "0"))
LOG_CHANNEL_ID    = int(os.getenv("LOG_CHANNEL_ID", "0"))
EASYSLIP_API_KEY  = os.getenv("EASYSLIP_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

BANK_NAME     = os.getenv("BANK_NAME", "กสิกรไทย (KBank)")
BANK_ACC_NAME = os.getenv("BANK_ACCOUNT_NAME", "INSIDEX SHOP")
BANK_ACC_NO   = os.getenv("BANK_ACCOUNT_NUMBER", "XXX-X-XXXXX-X")
TRUE_NUMBER   = os.getenv("TRUEMONEY_NUMBER", "0XX-XXX-XXXX")
PROMPTPAY_NO  = "0822099267"

PRICE_PER_ITEM    = int(os.getenv("RESHADE_PRICE", "39"))
PAYMENT_IMAGE_URL = "https://media.discordapp.net/attachments/1446487555091730544/1496205096734949516/39.png?ex=69f58f55&is=69f43dd5&hm=a06185f0dc2fee0564e92d3093ffa03f4fe47e23dd65c451e794cd416853c891&format=webp&quality=lossless&width=1037&height=1037&"
SHOP_BANNER_URL   = "https://cdn.discordapp.com/attachments/1446487555091730544/1499837254078697643/21.png?ex=69f63fcb&is=69f4ee4b&hm=03af46f901158128aa3e5758cca55bdd53059f754d9b5b834b2ba77f3503830f&"
TH     = timezone(timedelta(hours=7))
PURPLE = 0x7b2cbf

ACCEPTED_RECEIVERS = [
    "SIRIPOOM", "SIRIPHOOM", "สิริภูมิ", "INTAPANYA", "อินตะปัญญา",
]

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

DOWN_ROLE_CHANNELS = {
    "Moretime":   "CH_DOWN_MORETIME",
    "Dotashd.v1": "CH_DOWN_DOTASHD_V1",
    "Dotashd.v2": "CH_DOWN_DOTASHD_V2",
    "Dotashd.wf": "CH_DOWN_DOTASHD_WF",
    "Dotashd.v3": "CH_DOWN_DOTASHD_V3",
    "Dotasuns":   "CH_DOWN_DOTASUNS",
    "Dotashd.bw": "CH_DOWN_DOTASHD_BW",
    "Doinluv.01": "CH_DOWN_DOINLUV_01",
    "Doinluv.02": "CH_DOWN_DOINLUV_02",
    "Doinluv.03": "CH_DOWN_DOINLUV_03",
    "Doinluv.04": "CH_DOWN_DOINLUV_04",
}

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
#  HELPER: ดึง order_id จาก embed footer
# ─────────────────────────────────────────
def _get_order_id(self_order_id: str, interaction: discord.Interaction) -> str:
    if self_order_id:
        return self_order_id
    for embed in interaction.message.embeds:
        if embed.footer and embed.footer.text and "order:" in embed.footer.text:
            raw = embed.footer.text.split("|")[0]
            return raw.replace("order:", "").strip()
    return ""


# ─────────────────────────────────────────
#  EASYSLIP: สร้าง QR PromptPay
# ─────────────────────────────────────────
async def generate_promptpay_qr(amount: int) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.easyslip.com/v1/qr/generate",
                headers={
                    "Authorization": f"Bearer {EASYSLIP_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "type":   "PROMPTPAY",
                    "msisdn": PROMPTPAY_NO,
                    "amount": float(amount),
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        if data.get("status") != 200:
            return None
        return base64.b64decode(data["data"]["image"])
    except Exception:
        return None


# ─────────────────────────────────────────
#  VERIFY: Bank → EasySlip
# ─────────────────────────────────────────
def _check_receiver(payload: dict) -> tuple:
    rec      = payload.get("receiver", {})
    acc_name = rec.get("account", {}).get("name", {})
    th = (acc_name.get("th") or "").strip()
    en = (acc_name.get("en") or "").strip()
    display = f"{th} {en}".strip() or "ไม่มี"
    passed = any(
        kw.lower() in th.lower() or kw.lower() in en.lower()
        for kw in ACCEPTED_RECEIVERS
    )
    return passed, display


async def verify_bank_slip(image_url: str, expected_amount: int) -> dict:
    """ตรวจสลิปธนาคาร/PromptPay ด้วย EasySlip API"""
    async with aiohttp.ClientSession() as s:
        async with s.get(image_url) as r:
            if r.status != 200:
                return {"ok": False, "reason": "ดาวน์โหลดรูปไม่สำเร็จ"}
            img_bytes    = await r.read()
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]

    slip_hash = hashlib.sha256(img_bytes).hexdigest()
    if slip_hash in used_slip_hashes:
        return {"ok": False, "reason": "❌ สลิปนี้ถูกใช้ไปแล้ว"}

    now_th = datetime.now(TH)

    form = aiohttp.FormData()
    form.add_field("file", img_bytes, filename="slip.jpg", content_type=content_type)

    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://api.easyslip.com/v1/verify",
            headers={"Authorization": f"Bearer {EASYSLIP_API_KEY}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status not in (200, 400):
                return {"ok": False, "reason": f"EasySlip error {r.status}"}
            data = await r.json()

    if data.get("status") != 200:
        reason = data.get("message", "ไม่ทราบสาเหตุ")
        return {"ok": False, "reason": f"❌ ตรวจสลิปไม่ได้: {reason}"}

    payload = data.get("data", {})

    amt = float(payload.get("amount", {}).get("amount", 0))
    if round(amt) != expected_amount:
        return {"ok": False, "reason": f"❌ ยอดไม่ตรง (พบ ฿{amt:.0f} ต้อง ฿{expected_amount})"}

    passed, receiver_display = _check_receiver(payload)
    if not passed:
        return {"ok": False, "reason": f"❌ ชื่อผู้รับไม่ตรง (พบ: {receiver_display})"}

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
        "receiver":  receiver_display,
        "slip_time": dt_str,
    }


# ─────────────────────────────────────────
#  VERIFY: TrueMoney → Claude Vision
# ─────────────────────────────────────────
async def verify_truemoney_slip(image_url: str, expected_amount: int) -> dict:
    """ตรวจสลิป TrueMoney Wallet ด้วย Claude Vision"""
    async with aiohttp.ClientSession() as s:
        async with s.get(image_url) as r:
            if r.status != 200:
                return {"ok": False, "reason": "ดาวน์โหลดรูปไม่สำเร็จ"}
            img_bytes    = await r.read()
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]

    slip_hash = hashlib.sha256(img_bytes).hexdigest()
    if slip_hash in used_slip_hashes:
        return {"ok": False, "reason": "❌ สลิปนี้ถูกใช้ไปแล้ว"}

    now_th  = datetime.now(TH)
    img_b64 = base64.b64encode(img_bytes).decode()

    # ชื่อผู้รับที่ยอมรับ
    accepted_str = ", ".join(ACCEPTED_RECEIVERS)

    prompt = (
        f'คุณคือระบบตรวจสอบสลิป TrueMoney Wallet ของร้าน INSIDEX\n\n'
        f'ดูสลิปในรูปแล้วตอบ JSON บรรทัดเดียว ห้ามมีข้อความอื่น:\n\n'
        f'{{"found_amount":<ตัวเลขยอดโอน หรือ null>,'
        f'"found_receiver":"<ชื่อผู้รับ หรือ null>",'
        f'"found_datetime":"<YYYY-MM-DD HH:MM หรือ null>"}}\n\n'
        f'ข้อมูลที่ต้องตรวจ:\n'
        f'1. ยอดโอนต้องเท่ากับ {expected_amount} บาทพอดี\n'
        f'2. ชื่อผู้รับต้องมีคำใดคำหนึ่งจาก: {accepted_str}\n'
        f'3. เวลาในสลิปต้องไม่เกิน 30 นาทีจากปัจจุบัน ({now_th.strftime("%Y-%m-%d %H:%M")} เวลาไทย)\n\n'
        f'ตอบ JSON เท่านั้น'
    )

    headers = {
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      "claude-opus-4-5",
        "max_tokens": 256,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": img_b64}},
                {"type": "text",  "text":  prompt},
            ],
        }],
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers, json=body,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            if r.status != 200:
                return {"ok": False, "reason": f"Claude API error {r.status}"}
            data = await r.json()

    raw = data["content"][0]["text"].strip()
    raw = re.sub(r"```[a-z]*|```", "", raw).strip()

    try:
        res = json.loads(raw)
    except Exception:
        return {"ok": False, "reason": "อ่านสลิปไม่ได้ กรุณาส่งใหม่"}

    # เช็คยอด
    amt = res.get("found_amount")
    if amt is None:
        return {"ok": False, "reason": "❌ ไม่พบยอดเงินในสลิป"}
    if round(float(amt)) != expected_amount:
        return {"ok": False, "reason": f"❌ ยอดไม่ตรง (พบ ฿{amt} ต้อง ฿{expected_amount})"}

    # เช็คผู้รับ
    receiver = res.get("found_receiver") or ""
    if not any(kw.lower() in receiver.lower() for kw in ACCEPTED_RECEIVERS):
        return {"ok": False, "reason": f"❌ ชื่อผู้รับไม่ตรง (พบ: {receiver or 'ไม่มี'})"}

    # เช็คเวลา
    dt_str = res.get("found_datetime")
    if dt_str:
        try:
            slip_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TH)
            if (now_th - slip_dt).total_seconds() > 1800:
                return {"ok": False, "reason": f"❌ สลิปเกิน 30 นาที (เวลาสลิป: {dt_str})"}
        except Exception:
            pass

    used_slip_hashes.add(slip_hash)
    return {
        "ok":        True,
        "amount":    float(amt),
        "receiver":  receiver,
        "slip_time": dt_str or "-",
    }


# ─────────────────────────────────────────
#  ROUTER: เลือก verify ตาม payment_method
# ─────────────────────────────────────────
async def verify_slip(image_url: str, expected_amount: int, payment_method: str) -> dict:
    if payment_method == "truemoney":
        return await verify_truemoney_slip(image_url, expected_amount)
    else:
        return await verify_bank_slip(image_url, expected_amount)


# ─────────────────────────────────────────
#  BOT
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ─────────────────────────────────────────
#  STEP 1: เลือก Reshade ก่อนชำระ
# ─────────────────────────────────────────
class SelectReshadeView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=None)
        self.add_item(ReshadeSelectMenu(order_id))


class ReshadeSelectMenu(discord.ui.Select):
    def __init__(self, order_id: str):
        self.order_id = order_id
        options = [
            discord.SelectOption(label=r["label"], value=r["env"])
            for r in DOWN_ROLES
        ]
        super().__init__(
            placeholder="🎮 เลือก Reshade ที่ต้องการ (เลือกได้หลายตัว)...",
            min_values=1,
            max_values=len(DOWN_ROLES),
            options=options,
            custom_id="select_reshade_prebuy",
        )

    async def callback(self, interaction: discord.Interaction):
        order_id = self.order_id or _get_order_id("", interaction)
        order = pending_orders.get(order_id)
        if not order:
            return await interaction.response.send_message("❌ Order หมดอายุ กรุณาสั่งซื้อใหม่", ephemeral=True)

        chosen_envs   = self.values
        chosen_labels = [r["label"] for r in DOWN_ROLES if r["env"] in chosen_envs]
        qty           = len(chosen_labels)
        total         = qty * PRICE_PER_ITEM

        order["chosen_envs"]   = list(chosen_envs)
        order["chosen_labels"] = chosen_labels
        order["total_price"]   = total
        order["status"]        = "pending_payment"
        save_state()

        items_text = "\n".join(f"  🎨 {l}" for l in chosen_labels)
        embed = discord.Embed(
            title="🛒 สรุปรายการ",
            description=(
                f"**Reshade ที่เลือก ({qty} ตัว) :**\n{items_text}\n\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 **ยอดรวม : ฿{total}**\n"
                f"*(ตัวละ ฿{PRICE_PER_ITEM})*\n\n"
                f"🔖 Order ID : `{order_id}`\n\n"
                "เลือกวิธีชำระด้านล่าง"
            ),
            color=PURPLE,
        )
        embed.set_footer(text=f"order:{order_id}")
        await interaction.response.edit_message(embed=embed, view=PaymentView(order_id))


# ─────────────────────────────────────────
#  PAYMENT VIEWS
# ─────────────────────────────────────────
class PaymentView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="🏦 Bank / PromptPay", style=discord.ButtonStyle.primary, custom_id="payment_bank")
    async def bank(self, interaction: discord.Interaction, _: discord.ui.Button):
        order_id = _get_order_id(self.order_id, interaction)
        o = pending_orders.get(order_id)
        if not o:
            return await interaction.response.send_message("❌ Order หมดอายุ กรุณาสั่งซื้อใหม่", ephemeral=True)

        o["payment_method"] = "bank"
        o["status"]         = "waiting_slip"
        total = o["total_price"]

        await interaction.response.defer()
        qr_bytes = await generate_promptpay_qr(total)

        embed = discord.Embed(
            title="🏦 โอนผ่านธนาคาร / PromptPay",
            description=(
                f"**สินค้า :** 🎨 ReShade x{len(o['chosen_labels'])} ตัว\n"
                f"**ยอดรวม : ฿{total}**\n\n"
                f"```\nธนาคาร    : {BANK_NAME}\n"
                f"ชื่อบัญชี : {BANK_ACC_NAME}\n"
                f"เลขบัญชี  : {BANK_ACC_NO}\n```\n"
                f"🔖 Order ID : `{order_id}`\n\n"
                "📸 **ส่งรูปสลิปในห้องนี้ได้เลย**\n"
                "> ระบบตรวจอัตโนมัติ ~10 วินาที"
            ),
            color=PURPLE,
        )

        if qr_bytes:
            qr_file = discord.File(fp=BytesIO(qr_bytes), filename="promptpay_qr.png")
            embed.set_image(url="attachment://promptpay_qr.png")
            embed.set_footer(text=f"order:{order_id} | QR PromptPay ยอดรวม ฿{total}")
            await interaction.edit_original_response(
                embed=embed, attachments=[qr_file], view=CancelView(order_id),
            )
        else:
            embed.set_image(url=PAYMENT_IMAGE_URL)
            embed.set_footer(text=f"order:{order_id}")
            await interaction.edit_original_response(
                embed=embed, view=CancelView(order_id),
            )

    @discord.ui.button(label="💰 TrueMoney Wallet", style=discord.ButtonStyle.success, custom_id="payment_truemoney")
    async def truemoney(self, interaction: discord.Interaction, _: discord.ui.Button):
        order_id = _get_order_id(self.order_id, interaction)
        o = pending_orders.get(order_id)
        if not o:
            return await interaction.response.send_message("❌ Order หมดอายุ กรุณาสั่งซื้อใหม่", ephemeral=True)

        o["payment_method"] = "truemoney"
        o["status"]         = "waiting_slip"
        total = o["total_price"]

        embed = discord.Embed(
            title="💰 โอนผ่าน TrueMoney Wallet",
            description=(
                f"🎨 **ReShade x{len(o['chosen_labels'])} ตัว**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 **ยอดชำระ : ฿{total}**\n\n"
                f"```\nเบอร์รับเงิน : {TRUE_NUMBER}\n```\n"
                f"🔖 **Order ID :** `{order_id}`\n\n"
                "📸 **ส่งรูปสลิปในห้องนี้ได้เลย**\n"
                "> ระบบตรวจอัตโนมัติ ~15 วินาที"
            ),
            color=PURPLE,
        )
        embed.set_image(url=PAYMENT_IMAGE_URL)
        embed.set_footer(text=f"order:{order_id}")
        await interaction.response.edit_message(embed=embed, view=CancelView(order_id))


class CancelView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="❌ ยกเลิก Order", style=discord.ButtonStyle.danger, custom_id="cancel_order")
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        order_id = _get_order_id(self.order_id, interaction)
        pending_orders.pop(order_id, None)
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
        label="🎨 ซื้อ ReShade",
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
                f"❗ คุณมี order ค้างอยู่แล้ว → {existing.mention}", ephemeral=True
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
        "status":         "selecting",
        "payment_method": None,
        "chosen_envs":    [],
        "chosen_labels":  [],
        "total_price":    0,
        "timestamp":      datetime.now(TH).isoformat(),
    }
    save_state()

    embed = discord.Embed(
        title="🛒 สั่งซื้อ ReShade",
        description=(
            f"สวัสดี {member.mention}!\n\n"
            f"💰 **ราคาตัวละ ฿{PRICE_PER_ITEM}** (เลือกได้หลายตัว)\n\n"
            f"🔖 Order ID : `{order_id}`\n\n"
            "**เลือก Reshade ที่ต้องการด้านล่าง**\n"
            "*(เลือกได้หลายตัวพร้อมกัน)*"
        ),
        color=PURPLE,
    )
    embed.set_footer(text=f"order:{order_id}")
    await thread.send(embed=embed, view=SelectReshadeView(order_id))
    await interaction.response.send_message(
        f"<a:1134verifiedanimated:1495470992452227103> สร้างห้องส่วนตัวให้แล้ว กดที่นี่ → {thread.mention}",
        ephemeral=True
    )


# ─────────────────────────────────────────
#  GRANT
# ─────────────────────────────────────────
async def grant_reshade_and_finish(thread, guild, member, order_id, ocr, method, chosen_labels, chosen_envs):
    reshade_role = guild.get_role(ROLE_RESHADE_ID)
    if reshade_role:
        await member.add_roles(reshade_role, reason=f"INSIDEX {order_id}")

    roles_added = []
    for env in chosen_envs:
        role = guild.get_role(get_down_role_id(env))
        if role:
            roles_added.append(role)
    if roles_added:
        await member.add_roles(*roles_added, reason=f"INSIDEX {order_id}")

    chosen_labels_text = "\n".join(f"🎨 **{l}**" for l in chosen_labels)

    try:
        dm_lines = []
        for label in chosen_labels:
            ch_env = DOWN_ROLE_CHANNELS.get(label)
            ch_id  = int(os.getenv(ch_env, "0")) if ch_env else 0
            dm_lines.append(f"🎮 **{label}** → {'<#' + str(ch_id) + '>' if ch_id else '(ไม่ได้ตั้งค่าห้อง)'}")

        await member.send(embed=discord.Embed(
            title="<a:1134verifiedanimated:1495470992452227103> ได้รับยศ Reshade แล้ว!",
            description=(
                f"**Order ID:** `{order_id}`\n\n"
                f"ยศที่ได้รับ:\n{chosen_labels_text}\n\n"
                "เข้าห้องได้เลยครับ:\n" + "\n".join(dm_lines) +
                "\n\nขอบคุณที่ใช้บริการ **INSIDEX** 🙏"
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
        e.add_field(name="Reshade",   value=", ".join(chosen_labels),           inline=False)
        await log_ch.send(embed=e)

    await thread.send(
        content=member.mention,
        embed=discord.Embed(
            title="<a:1134verifiedanimated:1495470992452227103> เสร็จสมบูรณ์!",
            description=(
                f"**ได้รับยศแล้ว :**\n{chosen_labels_text}\n\n"
                "━━━━━━━━━━━━━━━━\n"
                "ขอบคุณที่ใช้บริการ **INSIDEX** 🙏\n"
                "> ห้องนี้จะถูกลบใน 5 วินาที"
            ),
            color=PURPLE,
        ),
    )

    await asyncio.sleep(5)
    try:
        await thread.delete()
    except Exception:
        pass

    user_threads.pop(member.id, None)
    save_state()


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
    bot.add_view(SelectReshadeView(""))
    bot.add_view(PaymentView(""))
    bot.add_view(CancelView(""))
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
                method = order["payment_method"]

                checking_msg = await message.reply(embed=discord.Embed(
                    title="🔍 กำลังตรวจสอบสลิป...",
                    description=(
                        "> กำลังอ่านข้อมูล\n"
                        "> กรุณารอสักครู่ "
                        f"({'~10' if method == 'bank' else '~15'} วินาที)"
                    ),
                    color=PURPLE,
                ))

                # route ตาม payment_method
                ocr = await verify_slip(att.url, order["total_price"], method)

                if ocr["ok"]:
                    order["status"] = "completed"
                    chosen_labels = order["chosen_labels"]
                    chosen_envs   = order["chosen_envs"]
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
                    await grant_reshade_and_finish(
                        thread=message.channel,
                        guild=message.guild,
                        member=message.author,
                        order_id=order_id,
                        ocr=ocr,
                        method=method,
                        chosen_labels=chosen_labels,
                        chosen_envs=chosen_envs,
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
            f"**ราคา : ฿{PRICE_PER_ITEM} / ตัว**\n\n"
            "ซื้อแล้วได้ :\n"
            "<a:1134verifiedanimated:1495470992452227103> ได้ยศ **Reshade** ทันที\n"
            "🎮 เลือกยศ **Reshade** ได้หลายตัวพร้อมกัน\n\n"
            "💳 รับชำระ : ธนาคาร / PromptPay / TrueMoney\n"
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
            value=(
                f"<@{o['user_id']}> | {o['status']} | "
                f"฿{o.get('total_price', 0)} | "
                f"{', '.join(o.get('chosen_labels', [])) or 'ยังไม่เลือก'}"
            ),
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
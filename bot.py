"""
INSIDEX Bot — ReShade Edition
- ซื้อแล้วได้ยศ Reshade ทันที
- จากนั้นเลือกยศ down- เสริม 1 ตัว
- ราคา 39.- รวมทุกอย่าง
- OCR: ตรวจยอด + ชื่อผู้รับ + เวลา ≤30 นาที (Claude Vision)
- ป้องกันสลิปซ้ำ SHA-256
- คำสั่งซื้อ: กดปุ่มใน embed เท่านั้น
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────
TOKEN             = os.getenv("DISCORD_TOKEN")
GUILD_ID          = int(os.getenv("GUILD_ID", "0"))
ADMIN_ROLE_ID     = int(os.getenv("ADMIN_ROLE_ID", "0"))
LOG_CHANNEL_ID    = int(os.getenv("LOG_CHANNEL_ID", "0"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

BANK_NAME     = os.getenv("BANK_NAME", "กสิกรไทย (KBank)")
BANK_ACC_NAME = os.getenv("BANK_ACCOUNT_NAME", "INSIDEX SHOP")
BANK_ACC_NO   = os.getenv("BANK_ACCOUNT_NUMBER", "XXX-X-XXXXX-X")
PROMPTPAY     = os.getenv("PROMPTPAY", "0XX-XXX-XXXX")
TRUE_NUMBER   = os.getenv("TRUEMONEY_NUMBER", "0XX-XXX-XXXX")

PRICE             = int(os.getenv("RESHADE_PRICE", "39"))
PAYMENT_IMAGE_URL = "https://media.discordapp.net/attachments/1446487555091730544/1496205096734949516/39.png?ex=69f58f55&is=69f43dd5&hm=a06185f0dc2fee0564e92d3093ffa03f4fe47e23dd65c451e794cd416853c891&format=webp&quality=lossless&width=1037&height=1037&"
SHOP_BANNER_URL   = "https://media.discordapp.net/attachments/1446487555091730544/1496205094138417262/34.png?ex=69f58f54&is=69f43dd4&hm=651c7c427f0a50c10f9da927f9efd792ef6ada0ca653c1f2e6ba089e011a5b24&=&format=webp&quality=lossless&width=928&height=283"
TH    = timezone(timedelta(hours=7))

# สีม่วงหลัก INSIDEX
PURPLE = 0x7b2cbf

# ─────────────────────────────────────────
#  ROLES
# ─────────────────────────────────────────
ROLE_RESHADE_ID = int(os.getenv("ROLE_RESHADE", "0"))

DOWN_ROLES = [
    {"label": "down-dotashd.v1", "env": "ROLE_DOWN_DOTASHD_V1"},
    {"label": "down-dotashd.v2", "env": "ROLE_DOWN_DOTASHD_V2"},
    {"label": "down-dotashd.wf",  "env": "ROLE_DOWN_DOTASHD_WF"},
    {"label": "down-dotashd.v3", "env": "ROLE_DOWN_DOTASHD_V3"},
    {"label": "down-dotasuns",   "env": "ROLE_DOWN_DOTASUNS"},
    {"label": "down-dotashd.bw", "env": "ROLE_DOWN_DOTASHD_BW"},
    {"label": "down-moretime",   "env": "ROLE_DOWN_MORETIME"},
    {"label": "down-doinluv.01", "env": "ROLE_DOWN_DOINLUV_01"},
    {"label": "down-doinluv.02", "env": "ROLE_DOWN_DOINLUV_02"},
    {"label": "down-doinluv.03", "env": "ROLE_DOWN_DOINLUV_03"},
    {"label": "down-doinluv.04", "env": "ROLE_DOWN_DOINLUV_04"},
]

def get_down_role_id(env_key: str) -> int:
    return int(os.getenv(env_key, "0"))

# ─────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────
pending_orders: dict = {}
used_slip_hashes: set = set()


# ─────────────────────────────────────────
#  OCR (Claude Vision)
# ─────────────────────────────────────────
async def ocr_slip(image_url: str) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(image_url) as r:
            if r.status != 200:
                return {"ok": False, "reason": "ดาวน์โหลดรูปไม่สำเร็จ"}
            img_bytes    = await r.read()
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]

    slip_hash = hashlib.sha256(img_bytes).hexdigest()
    if slip_hash in used_slip_hashes:
        return {"ok": False, "reason": "❌ สลิปนี้ถูกใช้ไปแล้ว"}

    img_b64 = base64.b64encode(img_bytes).decode()
    now_th  = datetime.now(TH)

    prompt = (
        f'คุณคือระบบตรวจสอบสลิปของร้าน INSIDEX\n\n'
        f'ดูสลิปในรูปแล้วตอบ JSON บรรทัดเดียว ห้ามมีข้อความอื่น:\n\n'
        f'{{"found_amount":<ตัวเลขยอดโอน หรือ null>,"found_receiver":"<ชื่อผู้รับ หรือ null>",'
        f'"found_datetime":"<YYYY-MM-DD HH:MM หรือ null>","slip_type":"<bank|truemoney|unknown>"}}\n\n'
        f'ข้อมูลที่ต้องตรวจ:\n'
        f'1. ยอดโอนต้องเท่ากับ {PRICE} บาทพอดี\n'
        f'2. ชื่อผู้รับต้องมีคำว่า "{BANK_ACC_NAME}"\n'
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
            timeout=aiohttp.ClientTimeout(total=30),
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

    amt = res.get("found_amount")
    if amt is None:
        return {"ok": False, "reason": "❌ ไม่พบยอดเงินในสลิป"}
    if int(amt) != PRICE:
        return {"ok": False, "reason": f"❌ ยอดไม่ตรง (พบ ฿{amt} ต้อง ฿{PRICE})"}

    receiver = res.get("found_receiver") or ""
    if BANK_ACC_NAME.lower() not in receiver.lower():
        return {"ok": False, "reason": f"❌ ชื่อผู้รับไม่ตรง (พบ : {receiver or 'ไม่มี'})"}

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
        "amount":    amt,
        "receiver":  receiver,
        "slip_time": dt_str,
        "slip_type": res.get("slip_type", "unknown"),
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
    def __init__(self, order_id: str):
        self.order_id = order_id
        options = [
            discord.SelectOption(label=r["label"], value=r["env"])
            for r in DOWN_ROLES
        ]
        super().__init__(
            placeholder="🎮 เลือก Reshade ที่ต้องการ...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen_env   = self.values[0]
        chosen_label = next(r["label"] for r in DOWN_ROLES if r["env"] == chosen_env)
        role_id      = get_down_role_id(chosen_env)
        role         = interaction.guild.get_role(role_id)

        if role:
            await interaction.user.add_roles(role, reason=f"INSIDEX down- {self.order_id}")

        log_ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_ch:
            await log_ch.send(embed=discord.Embed(
                title="🎮 Down Role Selected",
                description=(
                    f"**User:** {interaction.user.mention} ({interaction.user.name})\n"
                    f"**Order ID:** `{self.order_id}`\n"
                    f"**ยศที่เลือก:** `{chosen_label}`"
                ),
                color=PURPLE,
                timestamp=datetime.now(),
            ))

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎉 เสร็จสมบูรณ์!",
                description=(
                    f"ได้รับยศ **Reshade** + **{chosen_label}** แล้ว!\n\n"
                    "ขอบคุณที่ใช้บริการ INSIDEX 🙏\n"
                    "หากมีปัญหาติดต่อแอดมินได้เลย"
                ),
                color=PURPLE,
            ),
            view=None,
        )


class DownRoleView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=300)
        self.add_item(DownRoleSelect(order_id))


# ─────────────────────────────────────────
#  GRANT: Reshade ทันที + เปิด dropdown down-
# ─────────────────────────────────────────
async def grant_reshade_and_pick(channel, guild, member, order_id, ocr, method):
    reshade_role = guild.get_role(ROLE_RESHADE_ID)
    if reshade_role:
        await member.add_roles(reshade_role, reason=f"INSIDEX {order_id}")

    try:
        await member.send(embed=discord.Embed(
            title="✅ ได้รับยศ Reshade แล้ว!",
            description=(
                f"**Order ID:** `{order_id}`\n\n"
                "🎉 ยศ **Reshade** ถูกมอบให้แล้ว!\n"
                "กลับไปที่ server แล้วเลือกยศ **down-** เสริมที่ต้องการได้เลย 👇"
            ),
            color=PURPLE,
        ))
    except Exception:
        pass

    log_ch = guild.get_channel(LOG_CHANNEL_ID)
    if log_ch:
        e = discord.Embed(title="💳 Purchase — ReShade", color=PURPLE, timestamp=datetime.now())
        e.add_field(name="User",      value=f"{member.mention} ({member.name})", inline=True)
        e.add_field(name="ยอด",       value=f"฿{ocr['amount']}",                inline=True)
        e.add_field(name="วิธีชำระ", value=method,                              inline=True)
        e.add_field(name="Order ID",  value=f"`{order_id}`",                    inline=True)
        e.add_field(name="ผู้รับ",    value=ocr["receiver"],                    inline=True)
        e.add_field(name="เวลาสลิป", value=ocr.get("slip_time") or "-",        inline=True)
        await log_ch.send(embed=e)

    await channel.send(
        content=member.mention,
        embed=discord.Embed(
            title="🎮 เลือกยศ Reshade",
            description=(
                "ยศ **Reshade** ถูกมอบให้แล้ว ✅\n\n"
                "เลือกยศ **Reshade** ที่ต้องการ 1 ตัว\n"
                "*(รวมในราคา ฿39 แล้ว ไม่มีค่าใช้จ่ายเพิ่ม)*"
            ),
            color=PURPLE,
        ),
        view=DownRoleView(order_id),
    )


# ─────────────────────────────────────────
#  PAYMENT VIEWS
# ─────────────────────────────────────────
class PaymentView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=300)
        self.order_id = order_id

    def _order(self):
        return pending_orders.get(self.order_id)

    @discord.ui.button(label="🏦 Bank", style=discord.ButtonStyle.primary)
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
                f"```\nธนาคาร    : {BANK_NAME}\n"
                f"ชื่อบัญชี : {BANK_ACC_NAME}\n"
                f"เลขบัญชี  : {BANK_ACC_NO}\n```\n"
                f"🔖 Order ID : `{self.order_id}`\n\n"
                "📸 **ส่งรูปสลิปในข้อความถัดไปได้เลย**\n"
                "ระบบตรวจอัตโนมัติ ~10 วินาที"
            ),
            color=PURPLE,
        )
        embed.set_image(url=PAYMENT_IMAGE_URL)
        await interaction.response.edit_message(embed=embed, view=CancelView(self.order_id))

    @discord.ui.button(label="💰 TrueMoney Wallet", style=discord.ButtonStyle.success)
    async def truemoney(self, interaction: discord.Interaction, _: discord.ui.Button):
        o = self._order()
        if not o:
            return await interaction.response.send_message("❌ Order หมดอายุ", ephemeral=True)
        o["payment_method"] = "truemoney"
        o["status"]         = "waiting_slip"
        embed = discord.Embed(
            title="💰 โอนผ่าน TrueMoney Wallet",
            description=(
                f"**สินค้า :** 🎨 ReShade\n"
                f"**ยอด : ฿{PRICE}**\n\n"
                f"```\nเบอร์รับเงิน : {TRUE_NUMBER}\n```\n"
                f"🔖 Order ID : `{self.order_id}`\n\n"
                "📸 **ส่งรูปสลิปในข้อความถัดไปได้เลย**\n"
                "ระบบตรวจอัตโนมัติ ~10 วินาที"
            ),
            color=PURPLE,
        )
        embed.set_image(url=PAYMENT_IMAGE_URL)
        await interaction.response.edit_message(embed=embed, view=CancelView(self.order_id))


class CancelView(discord.ui.View):
    def __init__(self, order_id: str):
        super().__init__(timeout=600)
        self.order_id = order_id

    @discord.ui.button(label="❌ ยกเลิก Order", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        pending_orders.pop(self.order_id, None)
        await interaction.response.edit_message(content="❌ ยกเลิก Order แล้ว", embed=None, view=None)


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
    order_id = str(uuid.uuid4())[:8].upper()
    pending_orders[order_id] = {
        "user_id":        interaction.user.id,
        "user_name":      interaction.user.name,
        "channel_id":     interaction.channel_id,
        "status":         "pending_payment",
        "payment_method": None,
        "timestamp":      datetime.now(TH).isoformat(),
    }
    embed = discord.Embed(
        title="🛒 สั่งซื้อ ReShade",
        description=(
            f"**ราคา :** ฿{PRICE}\n"
            f"**Order ID :** `{order_id}`\n\n"
            "ซื้อแล้วได้ :\n"
            "✅ ยศ **Reshade** ทันที\n"
            "🎮 เลือกยศ **Reshade ที่ต้องการ** เสริม 1 ตัว (รวมในราคาแล้ว)\n\n"
            "เลือกวิธีชำระด้านล่าง"
        ),
        color=PURPLE,
    )
    await interaction.response.send_message(embed=embed, view=PaymentView(order_id), ephemeral=True)


# ─────────────────────────────────────────
#  EVENTS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ INSIDEX Bot: {bot.user}")
    bot.add_view(ShopEmbedView())
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image/"):
            entry = next(
                ((oid, o) for oid, o in pending_orders.items()
                 if o["user_id"] == message.author.id and o["status"] == "waiting_slip"),
                None,
            )
            if entry:
                order_id, order = entry
                order["status"] = "verifying"

                checking_msg = await message.reply(embed=discord.Embed(
                    title="🔍 กำลังตรวจสลิป...",
                    description="OCR กำลังอ่านข้อมูล รอ ~10 วินาที",
                    color=PURPLE,
                ))

                ocr = await ocr_slip(att.url)

                if ocr["ok"]:
                    order["status"] = "completed"
                    pending_orders.pop(order_id, None)
                    await checking_msg.edit(embed=discord.Embed(
                        title="✅ สลิปผ่าน! กำลังมอบยศ Reshade...",
                        description=(
                            f"**ยอด :** ฿{ocr['amount']}\n"
                            f"**ผู้รับ :** {ocr['receiver']}\n"
                            f"**เวลาสลิป :** {ocr.get('slip_time') or '-'}"
                        ),
                        color=PURPLE,
                    ))
                    await grant_reshade_and_pick(
                        channel=message.channel,
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
        title="🏪 INSIDEX SHOP",
        description=(
            "**🎨 ReShadeXautO**\n"
            "Visual Preset Customization — สวยงามระดับโปร\n"
            "พร้อม preset สำเร็จรูป + คู่มือติดตั้ง\n\n"
            f"**ราคา : ฿{PRICE}**\n\n"
            "ซื้อแล้วได้ :\n"
            "✅ ยศ **Reshade** ทันที\n"
            "🎮 เลือกยศ **Reshade** เสริม 1 ตัว (รวมในราคาแล้ว)\n\n"
            "💳 รับชำระ : ธนาคาร / PromptPay / TrueMoney\n"
            "⚡ ตรวจสลิปอัตโนมัติ — รับยศทันที!"
        ),
        color=PURPLE,
    )
    embed.set_footer(text="INSIDEX | Auto Slip Verification ✨")
    embed.set_image(url=SHOP_BANNER_URL)
    await interaction.channel.send(embed=embed, view=ShopEmbedView())
    await interaction.response.send_message("✅ วาง shop embed แล้ว", ephemeral=True)


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
        f"✅ มอบ **Reshade** + **{chosen_label}** ให้ {member.mention} แล้ว", ephemeral=True
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
import logging
import random
import io
import asyncio
import csv
from sqlalchemy.pool import NullPool
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, BigInteger, Boolean, func
from sqlalchemy.orm import declarative_base, sessionmaker
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler, PollAnswerHandler
)

from dotenv import load_dotenv
import os
load_dotenv()

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///hamshira_db.db")
REQUIRED_CHANNELS = ["@malakali_hamshiralar"]
ADMIN_ID = int(os.getenv("ADMIN_ID", "647129875"))

TEST_TIME_LIMIT = 15 * 60  # 15 daqiqa (soniyalarda)

# --- DB SETUP ---
Base = declarative_base()

if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
        pool_recycle=3600
    )

SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    full_name = Column(String)
    major = Column(String)
    phone = Column(String)
    points = Column(Float, default=3.0)
    tests_completed = Column(Integer, default=0)
    attempts = Column(Integer, default=3)
    full_access = Column(Boolean, default=False)

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    major = Column(String)
    text = Column(String)
    a = Column(String)
    b = Column(String)
    c = Column(String)
    d = Column(String)
    correct = Column(String)

Base.metadata.create_all(engine)

# --- STATES ---
NAME, MAJOR, PHONE, MENU, TESTING, CHANGE_MAJOR, ADMIN_ADD_Q, ADMIN_BROADCAST, ADMIN_GRANT, ADMIN_REVOKE = range(10)

# ==================== MENUS ====================
def main_menu():
    return ReplyKeyboardMarkup([
        ['📝 Testni boshlash'],
        ['📊 Mening statistikam'],
        ["✨ Qo'shimcha imkoniyatlar"],
        ['👨‍💻 Bog\'lanish']
    ], resize_keyboard=True)

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Savol qo'shish", callback_data='admin_add_question')],
        [InlineKeyboardButton("📊 Statistika", callback_data='admin_stats')],
        [InlineKeyboardButton("📢 Xabar yuborish", callback_data='admin_broadcast')],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data='admin_users_list')],
        [InlineKeyboardButton("⭐ Cheksiz huquq berish", callback_data='admin_grant')],
        [InlineKeyboardButton("🚫 Cheksiz huquqni olib tashlash", callback_data='admin_revoke')],
        [InlineKeyboardButton("📈 Test natijalari", callback_data='admin_test_results')],
        [InlineKeyboardButton("🗑️ Savol o'chirish", callback_data='admin_delete_question')],
        [InlineKeyboardButton("❌ Yopish", callback_data='admin_close')]
    ])

# ==================== OBUNA TEKSHIRUVI ====================
async def check_subscription(user_id, context):
    not_subscribed = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(ch, user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(ch)
        except Exception:
            not_subscribed.append(ch)
    return not_subscribed

async def send_subscription_warning(update: Update, context: ContextTypes.DEFAULT_TYPE, not_subscribed: list):
    keyboard = []
    for ch in not_subscribed:
        keyboard.append([InlineKeyboardButton(f"{ch} 📌", url=f"https://t.me/{ch[1:]}")])
    keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data='check_sub')])

    msg_text = (
        "⚠️ Siz kanallarimizdan chiqib ketganingiz sabab botdan to'liq foydalana olmaysiz.\n\n"
        "Iltimos avval obuna bo'ling👇"
    )
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await context.bot.send_message(update.effective_user.id, msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    not_subscribed = await check_subscription(user_id, context)
    if not_subscribed:
        await send_subscription_warning(update, context, not_subscribed)
        return False
    return True

# ==================== START & RO'YXATDAN O'TISH ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    not_subscribed = await check_subscription(user_id, context)
    if not_subscribed:
        keyboard = []
        for ch in not_subscribed:
            keyboard.append([InlineKeyboardButton(f"{ch} 📌", url=f"https://t.me/{ch[1:]}")])
        keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data='check_sub')])
        msg_text = "Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:"
        if query:
            try:
                await query.message.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
            await query.answer("Siz hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        else:
            await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    db.close()

    if not user:
        if not query and context.args:
            context.user_data['ref'] = context.args[0]
        if query:
            await query.message.delete()
            await context.bot.send_message(user_id, "Xush kelibsiz! Ism va familiyangizni kiriting:")
        else:
            await update.message.reply_text("Xush kelibsiz! Ism va familiyangizni kiriting:")
        return NAME

    if query:
        await query.message.delete()
        await context.bot.send_message(user_id, "Bosh sahifa:", reply_markup=main_menu())
    else:
        await update.message.reply_text("Bosh sahifa:", reply_markup=main_menu())
    return MENU

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('reg_state', NAME)

    if state == NAME:
        context.user_data['name'] = update.message.text
        btns = [['Hamshiralik ishi', 'Akusherlik ishi'], ['Patronaj hamshira', 'Davolash ishi']]
        await update.message.reply_text("Yo'nalishni tanlang:", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))
        context.user_data['reg_state'] = MAJOR
        return MAJOR

    elif state == MAJOR:
        context.user_data['major'] = update.message.text
        btn = [[KeyboardButton("Raqamni yuborish", request_contact=True)]]
        await update.message.reply_text("Telefon raqamingizni yuboring:", reply_markup=ReplyKeyboardMarkup(btn, resize_keyboard=True))
        context.user_data['reg_state'] = PHONE
        return PHONE

async def save_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = SessionLocal()

    try:
        new_user = User(
            id=user_id,
            full_name=context.user_data['name'],
            major=context.user_data['major'],
            phone=update.message.contact.phone_number,
            points=3.0,
            attempts=3,
            full_access=False
        )
        db.add(new_user)
        db.commit()

        ref_id = context.user_data.get('ref')
        if ref_id and ref_id.isdigit():
            context.user_data['pending_ref_id'] = ref_id

    finally:
        db.close()

    await update.message.reply_text(
        "Ro'yxatdan o'tdingiz! 3 ball va 3 urinish berildi.",
        reply_markup=main_menu()
    )
    return MENU

# ==================== TEST VAQT LIMITI ====================
async def test_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data['chat_id']
    user_id = job_data['user_id']
    user_data = context.application.user_data.get(user_id, {})

    if not user_data.get('test_active'):
        return

    user_data['test_active'] = False
    user_data['test_expired'] = True

    correct_count = user_data.get('correct_count', 0)
    q_idx = user_data.get('q_idx', 0)
    answered = q_idx
    c = correct_count
    w = answered - c
    score = float(c)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.points += score
            user.tests_completed += 1
            db.commit()
            await _process_referral_bonus(context, user_data, user_id)
    finally:
        db.close()

    await context.bot.send_message(
        chat_id=chat_id,
        text=(f"⏰ Vaqt tugadi! (15 daqiqa)\nTest avtomatik yakunlandi.\n\n"
              f"✅ To'g'ri: {c}\n❌ Noto'g'ri: {w}\n🏆 Ball: +{score:.0f}")
    )
    await context.bot.send_message(chat_id=chat_id, text="Asosiy menyu:", reply_markup=main_menu())

def cancel_test_timer(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    job_name = f"test_timer_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

async def _process_referral_bonus(context, user_data, invited_user_id):
    ref_id = user_data.get('pending_ref_id')
    if not ref_id:
        return
    db = SessionLocal()
    try:
        ref_user = db.query(User).filter(User.id == int(ref_id)).first()
        if ref_user:
            ref_user.points += 3.0
            ref_user.attempts += 3
            db.commit()
            try:
                await context.bot.send_message(
                    chat_id=ref_user.id,
                    text=(f"🎉 Tabriklaymiz! Do'stingiz sizning havolangiz orqali "
                          f"birinchi testini tugatdi va sizga 3 ball va +3 urinish qo'shildi!\n"
                          f"Umumiy urinishlar: {ref_user.attempts}")
                )
            except Exception:
                pass
    finally:
        db.close()
    user_data.pop('pending_ref_id', None)

# ==================== TEST LOGIKASI ====================
async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update, context):
        return MENU

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == update.effective_user.id).first()
        if not user:
            await update.message.reply_text("Iltimos avval /start buyrug'ini bosing.")
            return MENU

        if not user.full_access and user.attempts <= 0:
            await update.message.reply_text(
                "👀 Afsuski siz barcha test ishlash imkoniyatlaringizni ishlatib bo'libsiz.\n\n"
                "🔥 Botga yana ko'proq yaqinlaringizni taklif qilib, shuncha ko'p tayyorgarlik imkoniyatingizni oshiring.\n\n"
                "Har bitta hamshira tanishingizni referal havolangiz orqali loyihaga taklif qilsangiz +3 ta trenirovka imkoniyatini olasiz.\n\n"
                "Bilimingizni oshirishda davom eting! 👏 😊"
            )
            bot_info = await context.bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start={update.effective_user.id}"
            await update.message.reply_text(f"🤝 Havolangiz:\n{ref_link}", reply_markup=main_menu())
            return MENU

        questions = db.query(Question).filter(Question.major == user.major).all()

        if len(questions) < 25:
            await update.message.reply_text(
                f"Kechirasiz, '{user.major}' yo'nalishida faqat {len(questions)} ta savol bor. Kamida 25 ta kerak."
            )
            return MENU

        selected = random.sample(questions, 25)
        if not user.full_access:
            user.attempts -= 1
        db.commit()

        q_list = []
        for q in selected:
            options = [('a', q.a), ('b', q.b), ('c', q.c), ('d', q.d)]
            random.shuffle(options)

            new_correct = 'a'
            if q.correct:
                correct_key = q.correct.strip().lower()
                for i, (orig_key, _) in enumerate(options):
                    if orig_key == correct_key:
                        new_correct = ['a', 'b', 'c', 'd'][i]
                        break

            q_list.append((
                q.text,
                options[0][1], options[1][1], options[2][1], options[3][1],
                new_correct
            ))

    except Exception as e:
        db.close()
        # FIXED: Shows exact error in chat so you know why it failed
        await update.message.reply_text(f"❌ Xatolik yuz berdi: {str(e)}\n\nIltimos adminga xabar bering.")
        print(f"start_test error: {e}")
        return MENU
    finally:
        db.close()

    context.user_data['q_list'] = q_list
    context.user_data['q_idx'] = 0
    context.user_data['correct_count'] = 0
    context.user_data['test_active'] = True
    context.user_data['test_expired'] = False

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    context.user_data['chat_id'] = chat_id

    cancel_test_timer(context, user_id)
    context.job_queue.run_once(
        test_timeout_job,
        when=TEST_TIME_LIMIT,
        data={'chat_id': chat_id, 'user_id': user_id},
        name=f"test_timer_{user_id}"
    )

    await update.message.reply_text(
        f"⏱ Test boshlandi! Sizda 15 daqiqa vaqt bor.\n"
        f"Savollar soni: 25 ta. Omad! 💪"
    )

    return await send_question(update, context)


async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data['q_idx']
    q = context.user_data['q_list'][idx]

    if update and update.effective_chat:
        context.user_data['chat_id'] = update.effective_chat.id

    chat_id = context.user_data.get('chat_id')
    question_text = f"{idx + 1}/25 — {q[0]}"
    
    # FIXED: Prevent duplicate answers and limits which break Telegram Polls
    raw_options = [str(q[1])[:90].strip(), str(q[2])[:90].strip(), str(q[3])[:90].strip(), str(q[4])[:90].strip()]
    unique_options = []
    
    for i, opt in enumerate(raw_options):
        val = opt if opt else f"Bosh javob {i+1}"
        # If option already exists in the list, add invisible spaces to make it technically unique for Telegram
        while val in unique_options:
            val += " " 
        unique_options.append(val)

    mapping = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
    correct_idx = mapping.get(q[5], 0)

    try:
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=question_text[:300],
            options=unique_options,
            type='quiz',
            correct_option_id=correct_idx,
            is_anonymous=False,
            protect_content=True
        )
        context.user_data['current_poll_id'] = poll_msg.poll.id
        return TESTING
        
    except Exception as e:
        # FIXED: Catch exact error on send_poll
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"⚠️ Savolni yuborishda Telegram xatoligi yuz berdi: {str(e)}\n(Savol ID yoki matnida xato bor)"
        )
        print(f"send_poll error: {e}")
        # End test safely to not leave user stuck
        context.user_data['test_active'] = False
        return MENU


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    user_id = answer.user.id

    if context.user_data.get('test_expired'):
        return TESTING

    if not context.user_data.get('test_active'):
        return TESTING

    if answer.poll_id != context.user_data.get('current_poll_id'):
        return TESTING

    idx = context.user_data['q_idx']
    q = context.user_data['q_list'][idx]
    correct_option = q[5]
    mapping = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
    correct_idx = mapping.get(correct_option, 0)
    chosen_id = answer.option_ids[0] if answer.option_ids else -1

    if chosen_id == correct_idx:
        context.user_data['correct_count'] += 1

    context.user_data['q_idx'] += 1

    if context.user_data['q_idx'] < 25:
        return await send_question(update, context)
    else:
        context.user_data['test_active'] = False
        cancel_test_timer(context, user_id)

        c = context.user_data['correct_count']
        w = 25 - c
        score = float(c)

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.points += score
                user.tests_completed += 1
                db.commit()
        finally:
            db.close()

        await _process_referral_bonus(context, context.user_data, user_id)

        chat_id = context.user_data.get('chat_id')
        await context.bot.send_message(
            chat_id=chat_id,
            text=(f"🎉 Test tugadi!\n\n✅ To'g'ri: {c}\n❌ Noto'g'ri: {w}\n🏆 Ball: +{score:.0f}")
        )
        await context.bot.send_message(chat_id=chat_id, text="Asosiy menyu:", reply_markup=main_menu())
        return MENU


# ==================== FOYDALANUVCHI FUNKSIYALARI ====================
async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update, context):
        return MENU

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == update.effective_user.id).first()
        attempts_display = "Cheksiz (To'liq huquq)" if user.full_access else str(user.attempts)
        msg = (f"👤 {user.full_name}\n⚕️ Yo'nalish: {user.major}\n🏆 Ballar: {user.points:.0f}\n"
               f"✅ Testlar: {user.tests_completed}\n🎯 Urinishlar (Imkoniyat): {attempts_display}")
    finally:
        db.close()

    keyboard = [[InlineKeyboardButton("✏️ Yo'nalishni o'zgartirish", callback_data='change_major')]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def ask_new_major(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    btns = [['Hamshiralik ishi', 'Akusherlik ishi'], ['Patronaj hamshira', 'Davolash ishi']]
    await query.message.delete()
    await context.bot.send_message(update.effective_chat.id, "Yangi yo'nalishni tanlang:", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))
    return CHANGE_MAJOR

async def save_new_major(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_major = update.message.text
    db = SessionLocal()
    user = db.query(User).filter(User.id == update.effective_user.id).first()
    if user:
        user.major = new_major
        db.commit()
    db.close()
    await update.message.reply_text(f"Yo'nalishingiz muvaffaqiyatli '{new_major}' ga o'zgartirildi!", reply_markup=main_menu())
    return MENU

async def extra_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update, context):
        return MENU
    kbd = [[InlineKeyboardButton("🤝 Do'stlarni taklif qilish", callback_data='invite_friends')],
           [InlineKeyboardButton("💰 Qo'shimcha urinish sotib olish", callback_data='buy_attempts')]]
    await update.message.reply_text("Qo'shimcha imkoniyatlar:", reply_markup=InlineKeyboardMarkup(kbd))
    return MENU

async def handle_extra_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'invite_friends':
        try:
            db = SessionLocal()
            user = db.query(User).filter(User.id == update.effective_user.id).first()
            if user:
                bot_info = await context.bot.get_me()
                bot_name = bot_info.username
                ref_link = f"https://t.me/{bot_name}?start={user.id}"
                await query.message.reply_text(f"🤝 Do'stlaringizni taklif qilish uchun havola:\n\n{ref_link}\n\n"
                                               f"Har bir do'stingiz birinchi testini tugatganda sizga +3 ball va +3 urinish beriladi! 🎁")
        finally:
            db.close()
    elif query.data == 'buy_attempts':
        await query.message.reply_text("Qo'shimcha urinish sotib olish uchun admin bilan bog'laning: @AzizJurayev")
    return MENU

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Admin bilan bog'lanish uchun: @AzizJurayev")
    return MENU

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun!")
        return MENU

    msg_text = "🛠 Admin Panel\n\nQuyidagi funksiyalardan birini tanlang:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(msg_text, reply_markup=admin_menu())
    else:
        await update.message.reply_text(msg_text, reply_markup=admin_menu())
    return MENU

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'admin_add_question':
        await query.message.edit_text("➕ Yangi test qo'shish\n\nQuyidagi formatda yuboring:\n\nYo'nalish nomi\nSavol matni\nA javob\nB javob\nC javob\nD javob\na\n\nBekor qilish uchun /cancel yozing.")
        return ADMIN_ADD_Q
    elif query.data == 'admin_stats':
        await show_detailed_stats(update, context)
        return MENU
    elif query.data == 'admin_broadcast':
        await query.message.edit_text("📢 Xabar yuborish\n\nXabar matnini yuboring. Bu xabar BARCHA foydalanuvchilarga boradi.\n\nBekor qilish uchun /cancel yozing.")
        context.user_data['admin_action'] = 'waiting_broadcast'
        return ADMIN_BROADCAST
    elif query.data == 'admin_users_list':
        await admin_users_list(update, context)
        return MENU
    elif query.data == 'admin_grant':
        await query.message.edit_text("⭐ Cheksiz huquq berish\n\nFoydalanuvchi ID sini yuboring.\nMasalan: 123456789\n\nBekor qilish uchun /cancel yozing.")
        context.user_data['admin_action'] = 'waiting_grant'
        return ADMIN_GRANT
    elif query.data == 'admin_revoke':
        await query.message.edit_text("🚫 Cheksiz huquqni olib tashlash\n\nFoydalanuvchi ID sini yuboring.\nMasalan: 123456789\n\nBekor qilish uchun /cancel yozing.")
        context.user_data['admin_action'] = 'waiting_revoke'
        return ADMIN_REVOKE
    elif query.data == 'admin_test_results':
        await show_test_results(update, context)
        return MENU
    elif query.data == 'admin_delete_question':
        await show_questions_to_delete(update, context)
        return MENU
    elif query.data == 'admin_close':
        await query.message.delete()
        return MENU
    return MENU

async def show_detailed_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    question_stats = db.query(Question.major, func.count(Question.id)).group_by(Question.major).all()
    total_users = db.query(User).count()
    users_by_major = db.query(User.major, func.count(User.id)).group_by(User.major).all()
    total_tests = db.query(func.sum(User.tests_completed)).scalar() or 0
    total_points = db.query(func.sum(User.points)).scalar() or 0
    unlimited_users = db.query(User).filter(User.full_access == True).count()
    db.close()

    stats_text = "📊 Bot Statistikasi\n\n🔹 Savollar statistikasi:\n"
    for major, count in question_stats:
        stats_text += f"   • {major}: {count} ta\n"
    stats_text += f"\n🔹 Foydalanuvchilar:\n   • Jami: {total_users} ta\n   • Cheksiz huquqli: {unlimited_users} ta\n"
    stats_text += f"\n🔹 Yo'nalishlar bo'yicha:\n"
    for major, count in users_by_major:
        stats_text += f"   • {major}: {count} ta\n"
    stats_text += f"\n🔹 Test natijalari:\n   • Jami testlar: {total_tests}\n   • Jami ballar: {total_points:.0f}\n"

    if update.callback_query:
        await update.callback_query.message.edit_text(stats_text)
    else:
        await update.message.reply_text(stats_text)

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    if not users:
        await update.callback_query.message.edit_text("📭 Bazada hech qanday foydalanuvchi yo'q.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Ism', 'Telefon', "Yo'nalish", 'Ballar', 'Testlar soni', 'Urinishlar', 'Cheksiz huquq'])
    for u in users:
        writer.writerow([u.id, u.full_name, u.phone, u.major, u.points, u.tests_completed, u.attempts if not u.full_access else 'Cheksiz', 'Ha' if u.full_access else "Yo'q"])

    output.seek(0)
    doc = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    doc.name = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    await context.bot.send_document(chat_id=update.effective_user.id, document=doc, caption=f"👥 Foydalanuvchilar ro'yxati\nJami: {len(users)} ta")
    await update.callback_query.message.edit_text("✅ Foydalanuvchilar ro'yxati yuborildi!")

async def show_test_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    top_users = db.query(User).order_by(User.points.desc()).limit(10).all()
    db.close()
    if not top_users:
        await update.callback_query.message.edit_text("📊 Hali hech qanday test natijalari yo'q.")
        return

    result_text = "🏆 Top 10 foydalanuvchilar:\n\n"
    for i, user in enumerate(top_users, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        result_text += f"{medal} {user.full_name[:20]}\n   • Ballar: {user.points:.0f}\n   • Testlar: {user.tests_completed}\n   • Yo'nalish: {user.major}\n\n"
    await update.callback_query.message.edit_text(result_text)

async def show_questions_to_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    db = SessionLocal()
    majors = db.query(Question.major).distinct().all()
    db.close()
    if not majors:
        await query.message.edit_text("📭 Bazada savollar yo'q.")
        return MENU
    keyboard = [[InlineKeyboardButton(f"📚 {major}", callback_data=f'del_maj_{major}')] for (major,) in majors]
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data='admin_panel_back')])
    await query.message.edit_text("🗑️ Savol o'chirish\n\nYo'nalishni tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def show_questions_by_major_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    major = query.data.replace('del_maj_', '')
    db = SessionLocal()
    questions = db.query(Question).filter(Question.major == major).all()
    db.close()

    if not questions:
        await query.message.edit_text(f"📭 {major} yo'nalishida savollar yo'q.")
        return MENU

    keyboard = [[InlineKeyboardButton(f"❌ {q.text[:40]}...", callback_data=f'del_question_{q.id}')] for q in questions]
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data='admin_delete_question')])
    await query.message.edit_text(f"🗑️ {major} - Savol o'chirish\n\nQuyidagi savollardan birini tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))
    return MENU

async def delete_question_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        question_id = int(query.data.replace('del_question_', ''))
        db = SessionLocal()
        question = db.query(Question).filter(Question.id == question_id).first()
        if question:
            major = question.major
            db.delete(question)
            db.commit()
            db.close()
            await query.answer("✅ Savol o'chirildi!", show_alert=True)
            query.data = f'del_maj_{major}'
            await show_questions_by_major_simple(update, context)
        else:
            db.close()
            await query.answer("❌ Savol topilmadi!", show_alert=True)
    except Exception as e:
        await query.answer(f"Xatolik: {str(e)}", show_alert=True)
    return MENU

async def admin_save_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return MENU
    text = update.message.text
    if text == '/cancel':
        await update.message.reply_text("Bekor qilindi.", reply_markup=main_menu())
        return MENU

    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if len(lines) < 7:
        await update.message.reply_text("Xato format! 7 qator bo'lishi kerak. Qayta urinib ko'ring yoki /cancel.")
        return ADMIN_ADD_Q

    major, q_text, a, b, c, d, correct = lines[0], lines[1], lines[2], lines[3], lines[4], lines[5], lines[6].lower()
    if correct not in ['a', 'b', 'c', 'd']:
        await update.message.reply_text("To'g'ri javob xato. 'a', 'b', 'c' yoki 'd' bo'lishi kerak.")
        return ADMIN_ADD_Q

    db = SessionLocal()
    db.add(Question(major=major, text=q_text, a=a, b=b, c=c, d=d, correct=correct))
    db.commit()
    db.close()

    await update.message.reply_text(f"✅ Test muvaffaqiyatli qo'shildi ({major})!\n\nYana qo'shishingiz yoki /cancel yozishingiz mumkin.")
    return ADMIN_ADD_Q

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get('admin_action')
    if action == 'waiting_broadcast':
        text = update.message.text
        db = SessionLocal()
        users = db.query(User.id).all()
        db.close()
        success, failed = 0, 0
        status_msg = await update.message.reply_text("📨 Xabar yuborilmoqda...")
        for (uid,) in users:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                success += 1
            except:
                failed += 1
            if success % 10 == 0: await asyncio.sleep(0.5)
        await status_msg.edit_text(f"✅ Xabar yuborildi!\n✅ Muvaffaqiyatli: {success}\n❌ Xato: {failed}")
        context.user_data.pop('admin_action', None)
        await admin_panel(update, context)
        return MENU
    elif action in ['waiting_grant', 'waiting_revoke']:
        try:
            target_id = int(update.message.text)
            db = SessionLocal()
            user = db.query(User).filter(User.id == target_id).first()
            if user:
                user.full_access = (action == 'waiting_grant')
                db.commit()
                if action == 'waiting_grant':
                    await update.message.reply_text(f"✅ {target_id} ga cheksiz huquq berildi!")
                    try: await context.bot.send_message(chat_id=target_id, text="🌟 Tabriklaymiz! Sizga testlarni cheksiz ishlash huquqi berildi!")
                    except: pass
                else:
                    await update.message.reply_text(f"✅ {target_id} ning cheksiz huquqi olib tashlandi!")
            else:
                await update.message.reply_text("❌ Foydalanuvchi topilmadi!")
            db.close()
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID format!")
        context.user_data.pop('admin_action', None)
        await admin_panel(update, context)
        return MENU
    return MENU

# ==================== MAIN ====================
def main():
    application = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('admin', admin_panel),
            CallbackQueryHandler(start, pattern='^check_sub$'),
            CallbackQueryHandler(admin_callback_handler, pattern='^admin_'),
            CallbackQueryHandler(show_questions_by_major_simple, pattern='^del_maj_'),
            CallbackQueryHandler(delete_question_simple, pattern='^del_question_'),
            CallbackQueryHandler(show_questions_to_delete, pattern='^admin_delete_question$'),
            CallbackQueryHandler(admin_panel, pattern='^admin_panel_back$')
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration)],
            MAJOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration)],
            PHONE: [MessageHandler(filters.CONTACT, save_user)],
            MENU: [
                MessageHandler(filters.Regex('^📝 Testni boshlash$'), start_test),
                MessageHandler(filters.Regex('^📊 Mening statistikam$'), get_stats),
                MessageHandler(filters.Regex("^✨ Qo'shimcha imkoniyatlar$"), extra_options),
                MessageHandler(filters.Regex("^👨‍💻 Bog'lanish$"), contact_admin),
                CallbackQueryHandler(ask_new_major, pattern='^change_major$'),
                CallbackQueryHandler(handle_extra_callbacks, pattern='^(invite_friends|buy_attempts)$'),
                CallbackQueryHandler(admin_callback_handler, pattern='^admin_'),
                CallbackQueryHandler(show_questions_by_major_simple, pattern='^del_maj_'),
                CallbackQueryHandler(delete_question_simple, pattern='^del_question_'),
                CallbackQueryHandler(show_questions_to_delete, pattern='^admin_delete_question$'),
                CallbackQueryHandler(admin_panel, pattern='^admin_panel_back$'),
                CommandHandler('admin', admin_panel),
            ],
            CHANGE_MAJOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_major)],
            ADMIN_ADD_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_question)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text_input)],
            ADMIN_GRANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text_input)],
            ADMIN_REVOKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text_input)],
            TESTING: [PollAnswerHandler(handle_answer)]
        },
        fallbacks=[
            CommandHandler('start', start),
            CommandHandler('admin', admin_panel),
        ],
        per_chat=False
    )

    application.add_handler(conv)
    print("🤖 Bot ishga tushdi...")
    print(f"Admin ID: {ADMIN_ID}")
    print("Press Ctrl+C to stop")
    application.run_polling()

if __name__ == '__main__':
    main()

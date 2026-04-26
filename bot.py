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

# --- DB SETUP ---
Base = declarative_base()

engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,          # SQLite uchun eng yaxshi – har bir so'rov yangi ulanish
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
))

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

# ==================== MAIN MENU ====================
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
    ])  # Removed resize_keyboard parameter

# ==================== SUBSCRIPTION CHECK ====================
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

# ==================== START & REGISTRATION ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id
        
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()

    not_subscribed = await check_subscription(user_id, context)

    if not_subscribed:
        keyboard = []
        for ch in not_subscribed:
            keyboard.append([InlineKeyboardButton(f"Obuna bo'lish: {ch}", url=f"https://t.me/{ch[1:]}")])
        keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data='check_sub')])
        
        msg_text = "Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:"
        db.close()
        if query:
            try:
                await query.message.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
            await query.answer("Siz hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        else:
            await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    if not user:
        if not query and context.args: 
            context.user_data['ref'] = context.args[0]
        db.close()
        if query:
            await query.message.delete()
            await context.bot.send_message(user_id, "Xush kelibsiz! Ism va familiyangizni kiriting:")
        else:
            await update.message.reply_text("Xush kelibsiz! Ism va familiyangizni kiriting:")
        return NAME
    
    db.close()
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
        
        ref_id = context.user_data.get('ref')
        if ref_id and ref_id.isdigit():
            ref_user = db.query(User).filter(User.id == int(ref_id)).first()
            if ref_user: 
                ref_user.points += 3.0
                ref_user.attempts += 3
                try:
                    await context.bot.send_message(
                        chat_id=ref_user.id,
                        text=f"🎉 Tabriklaymiz! do'stingiz sizning havolangiz orqali ro'yxatdan o'tdi va sizga 3 ball va qo'shimcha 3 urinish qo'shildi!\nUmumiy urinishlar: {ref_user.attempts}"
                    )
                except Exception:
                    pass
        db.commit()
    finally:
        db.close()
    await update.message.reply_text("Ro'yxatdan o'tdingiz! 3 ball va 3 urinish berildi.", reply_markup=main_menu())
    return MENU

# ==================== TEST LOGIC ====================
async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == update.effective_user.id).first()
        if not user:
            await update.message.reply_text("Iltimos avval /start buyrug'ini bosing.")
            return MENU
        
        questions = db.query(Question).filter(Question.major == user.major).all()
        
        if len(questions) < 25:
            await update.message.reply_text(f"Kechirasiz, '{user.major}' yo'nalishida faqat {len(questions)} ta savol bor. Kamida 25 ta kerak.")
            return MENU
        
        selected = random.sample(questions, 25)
        if not user.full_access:
            user.attempts -= 1
        db.commit()
        
        # === Build q_list BEFORE closing session ===
        q_list = []
        for q in selected:
            # Access all attributes while session is still active
            options = [
                ('a', q.a),
                ('b', q.b),
                ('c', q.c),
                ('d', q.d)
            ]
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
                options[0][1],
                options[1][1],
                options[2][1],
                options[3][1],
                new_correct
            ))
        
        db.close()  # <-- NOW close after extracting all data
        
    except Exception as e:
        db.close()
        await update.message.reply_text("Xatolik yuz berdi. Iltimos keyinroq urinib ko'ring.")
        print(f"start_test error: {e}")
        return MENU
    
    context.user_data['q_list'] = q_list
    context.user_data['q_idx'] = 0
    context.user_data['correct_count'] = 0
    
    return await send_question(update, context)

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data['q_idx']
    q = context.user_data['q_list'][idx]
    
    if update and update.effective_chat:
        context.user_data['chat_id'] = update.effective_chat.id
        
    chat_id = context.user_data.get('chat_id')
    
    question_text = f"{idx+1}-savol: {q[0]}"
    options = [str(q[1])[:100] or "-", str(q[2])[:100] or "-", str(q[3])[:100] or "-", str(q[4])[:100] or "-"]
    
    mapping = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
    correct_idx = mapping.get(q[5], 0)
    
    poll_msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=question_text[:300],
        options=options,
        type='quiz',
        correct_option_id=correct_idx,
        is_anonymous=False
    )
    
    context.user_data['current_poll_id'] = poll_msg.poll.id
    return TESTING

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    
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
        c = context.user_data['correct_count']
        w = 25 - c
        score = float(c)
        
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == answer.user.id).first()
            if user:
                user.points += score
                user.tests_completed += 1
                db.commit()
        finally:
            db.close()
            
        chat_id = context.user_data.get('chat_id')
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Test tugadi!\n✅ To'g'ri: {c}\n❌ Noto'g'ri: {w}\n🏆 Ball: +{score:.0f}"
        )
        await context.bot.send_message(chat_id=chat_id, text="Asosiy menyu:", reply_markup=main_menu())
        return MENU

# ==================== USER FUNCTIONS ====================
async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == update.effective_user.id).first()
        attempts_display = "Cheksiz (To'liq huquq)" if user.full_access else str(user.attempts)
        msg = (f"👤 {user.full_name}\n⚕️ Yo'nalish: {user.major}\n"
               f"🏆 Ballar: {user.points:.0f}\n✅ Testlar: {user.tests_completed}\n"
               f"🎯 Urinishlar (Imkoniyat): {attempts_display}")
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
    await context.bot.send_message(
         update.effective_chat.id, 
         "Yangi yo'nalishni tanlang:", 
         reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True)
    )
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
    kbd = [
        [InlineKeyboardButton("🤝 Do'stlarni taklif qilish", callback_data='invite_friends')],
        [InlineKeyboardButton("💰 Qo'shimcha urinish sotib olish", callback_data='buy_attempts')]
    ]
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
                await query.message.reply_text(f"🤝 Do'stlaringizni taklif qilish uchun havola:\n\n{ref_link}")
            else:
                await query.message.reply_text("Kechirasiz, sizning ma'lumotlaringiz topilmadi, iltimos /start buyrug'ini yozing.")
        except Exception:
            await query.message.reply_text("Kechirasiz, xatolik yuz berdi. Iltimos keyinroq urinib ko'ring.")
    elif query.data == 'buy_attempts':
        await query.message.reply_text("Qo'shimcha urinish sotib olish uchun admin bilan bog'laning: @AzizJurayev")
    return MENU

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Admin bilan bog'lanish uchun shu manzilga murojaat qiling: @AzizJurayev")
    return MENU

# ==================== ENHANCED ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun!")
        return MENU
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(
            "🛠 *Admin Panel*\n\nQuyidagi funksiyalardan birini tanlang:",
            reply_markup=admin_menu(),
            parse_mode='HTML'  # Changed from 'Markdown' to 'HTML'
        )
    else:
        await update.message.reply_text(
            "🛠 *Admin Panel*\n\nQuyidagi funksiyalardan birini tanlang:",
            reply_markup=admin_menu(),
            parse_mode='HTML'  # Changed from 'Markdown' to 'HTML'
        )
    return MENU

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin menu callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'admin_add_question':
        await query.message.edit_text(
            "➕ *Yangi test qo'shish*\n\n"
            "Quyidagi formatda yuboring:\n\n"
            "`Yo'nalish nomi\nSavol matni\nA javob\nB javob\nC javob\nD javob\na`\n\n"
            "Bekor qilish uchun /cancel yozing.",
            parse_mode='Markdown'
        )
        return ADMIN_ADD_Q
        
    elif query.data == 'admin_stats':
        await show_detailed_stats(update, context)
        return MENU
        
    elif query.data == 'admin_broadcast':
        await query.message.edit_text(
            "📢 *Xabar yuborish*\n\n"
            "Xabar matnini yuboring. Bu xabar BARCHA foydalanuvchilarga boradi.\n\n"
            "Bekor qilish uchun /cancel yozing.",
            parse_mode='Markdown'
        )
        context.user_data['admin_action'] = 'waiting_broadcast'
        return ADMIN_BROADCAST
        
    elif query.data == 'admin_users_list':
        await admin_users_list(update, context)
        return MENU
        
    elif query.data == 'admin_grant':
        await query.message.edit_text(
            "⭐ *Cheksiz huquq berish*\n\n"
            "Foydalanuvchi ID sini yuboring.\n"
            "Masalan: `123456789`\n\n"
            "Bekor qilish uchun /cancel yozing.",
            parse_mode='Markdown'
        )
        context.user_data['admin_action'] = 'waiting_grant'
        return ADMIN_GRANT
        
    elif query.data == 'admin_revoke':
        await query.message.edit_text(
            "🚫 *Cheksiz huquqni olib tashlash*\n\n"
            "Foydalanuvchi ID sini yuboring.\n"
            "Masalan: `123456789`\n\n"
            "Bekor qilish uchun /cancel yozing.",
            parse_mode='Markdown'
        )
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
    
    stats_text = "📊 *Bot Statistikasi*\n\n"
    stats_text += "🔹 *Savollar statistikasi:*\n"
    for major, count in question_stats:
        stats_text += f"   • {major}: {count} ta\n"
    
    stats_text += f"\n🔹 *Foydalanuvchilar:*\n"
    stats_text += f"   • Jami: {total_users} ta\n"
    stats_text += f"   • Cheksiz huquqli: {unlimited_users} ta\n"
    
    stats_text += f"\n🔹 *Yo'nalishlar bo'yicha:*\n"
    for major, count in users_by_major:
        stats_text += f"   • {major}: {count} ta\n"
    
    stats_text += f"\n🔹 *Test natijalari:*\n"
    stats_text += f"   • Jami testlar: {total_tests}\n"
    stats_text += f"   • Jami ballar: {total_points:.0f}\n"
    stats_text += f"   • O'rtacha ball: {total_points/total_users if total_users > 0 else 0:.1f}\n"
    
    if update.callback_query:
        await update.callback_query.message.edit_text(stats_text, parse_mode='Markdown')
    else:
        await update.message.reply_text(stats_text, parse_mode='Markdown')

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    
    if not users:
        await update.callback_query.message.edit_text("📭 Bazada hech qanday foydalanuvchi yo'q.")
        return
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Ism', 'Telefon', 'Yo\'nalish', 'Ballar', 'Testlar soni', 'Urinishlar', 'Cheksiz huquq'])
    
    for u in users:
        writer.writerow([
            u.id, u.full_name, u.phone, u.major, 
            u.points, u.tests_completed, u.attempts if not u.full_access else 'Cheksiz',
            'Ha' if u.full_access else 'Yo\'q'
        ])
    
    output.seek(0)
    doc = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    doc.name = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    await context.bot.send_document(
        chat_id=update.effective_user.id, 
        document=doc, 
        caption=f"👥 Foydalanuvchilar ro'yxati\nJami: {len(users)} ta"
    )
    
    await update.callback_query.message.edit_text("✅ Foydalanuvchilar ro'yxati yuborildi!")

async def show_test_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    top_users = db.query(User).order_by(User.points.desc()).limit(10).all()
    db.close()
    
    if not top_users:
        await update.callback_query.message.edit_text("📊 Hali hech qanday test natijalari yo'q.")
        return
    
    result_text = "🏆 *Top 10 foydalanuvchilar:*\n\n"
    for i, user in enumerate(top_users, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        result_text += f"{medal} *{user.full_name[:20]}*\n"
        result_text += f"   • Ballar: {user.points:.0f}\n"
        result_text += f"   • Testlar: {user.tests_completed}\n"
        result_text += f"   • Yo'nalish: {user.major}\n\n"
    
    await update.callback_query.message.edit_text(result_text, parse_mode='Markdown')

async def show_questions_to_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show questions list for deletion"""
    query = update.callback_query
    await query.answer()
    
    db = SessionLocal()
    # Get all majors
    majors = db.query(Question.major).distinct().all()
    db.close()
    
    if not majors:
        await query.message.edit_text("📭 Bazada savollar yo'q.")
        return MENU
    
    keyboard = []
    for (major,) in majors:
        # Use simple callback data without special characters
        keyboard.append([InlineKeyboardButton(f"📚 {major}", callback_data=f'del_maj_{major}')])
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data='admin_panel_back')])
    
    await query.message.edit_text(
        "🗑️ *Savol o'chirish*\n\nYo'nalishni tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return MENU

async def show_questions_by_major_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show questions of selected major for deletion"""
    query = update.callback_query
    await query.answer()
    
    # Get major from callback data
    major = query.data.replace('del_maj_', '')
    
    db = SessionLocal()
    questions = db.query(Question).filter(Question.major == major).all()
    db.close()
    
    if not questions:
        await query.message.edit_text(f"📭 {major} yo'nalishida savollar yo'q.")
        return MENU
    
    keyboard = []
    for q in questions:
        q_text = q.text[:40] + "..." if len(q.text) > 40 else q.text
        keyboard.append([InlineKeyboardButton(
            f"❌ {q_text}", 
            callback_data=f'del_question_{q.id}'
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data='admin_delete_question')])
    
    await query.message.edit_text(
        f"🗑️ *{major}* - Savol o'chirish\n\nQuyidagi savollardan birini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return MENU

async def delete_question_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete selected question"""
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
            await query.answer("✅ Savol o'chirildi!", show_alert=True)
            db.close()
            
            # Go back to the questions list of the same major
            # Create a new query to show updated list
            query.data = f'del_maj_{major}'
            await show_questions_by_major_simple(update, context)
        else:
            db.close()
            await query.answer("❌ Savol topilmadi!", show_alert=True)
            
    except Exception as e:
        await query.answer(f"Xatolik: {str(e)}", show_alert=True)
    
    return MENU

async def handle_delete_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination in delete questions"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace('delete_page_', '')
    page, major = data.split('_', 1)
    context.user_data['delete_page'] = int(page)
    
    # Show questions for the new page
    query.data = f'delete_major_{major}'
    await show_questions_by_major(update, context)
    return MENU

async def admin_save_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return MENU
        
    text = update.message.text
    if text == '/cancel':
        await update.message.reply_text("Bekor qilindi. Bosh sahifa:", reply_markup=main_menu())
        return MENU
        
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if len(lines) < 7:
        await update.message.reply_text(
            "Xato format! 7 qator bo'lishi kerak. Iltimos qayta urinib ko'ring yoki bekor qilish uchun /cancel yozing."
        )
        return ADMIN_ADD_Q
        
    major = lines[0]
    q_text = lines[1]
    a = lines[2]
    b = lines[3]
    c = lines[4]
    d = lines[5]
    correct = lines[6].lower().strip()
    
    if correct not in ['a', 'b', 'c', 'd']:
        await update.message.reply_text("To'g'ri javob xato kiritildi. 'a', 'b', 'c' yoki 'd' bo'lishi kerak.")
        return ADMIN_ADD_Q
        
    db = SessionLocal()
    new_q = Question(
        major=major,
        text=q_text,
        a=a,
        b=b,
        c=c,
        d=d,
        correct=correct
    )
    db.add(new_q)
    db.commit()
    db.close()
    
    await update.message.reply_text(f"✅ Test muvaffaqiyatli qo'shildi ({major})!\n\nYana test qo'shishingiz yoki /cancel yozishingiz mumkin.")
    return ADMIN_ADD_Q

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get('admin_action')
    
    if action == 'waiting_broadcast':
        text = update.message.text
        db = SessionLocal()
        users = db.query(User.id).all()
        db.close()
        
        success = 0
        failed = 0
        status_msg = await update.message.reply_text("📨 Xabar yuborilmoqda...")
        
        for (uid,) in users:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                success += 1
            except Exception:
                failed += 1
            
            if success % 10 == 0:
                await asyncio.sleep(0.5)
        
        await status_msg.edit_text(f"✅ Xabar yuborildi!\n✅ Muvaffaqiyatli: {success}\n❌ Xato: {failed}")
        context.user_data.pop('admin_action', None)
        await admin_panel(update, context)
        return MENU
        
    elif action == 'waiting_grant':
        try:
            target_id = int(update.message.text)
            db = SessionLocal()
            user = db.query(User).filter(User.id == target_id).first()
            
            if user:
                user.full_access = True
                db.commit()
                await update.message.reply_text(f"✅ {target_id} ga cheksiz huquq berildi!")
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="🌟 Tabriklaymiz! Sizga testlarni cheksiz ishlash huquqi berildi!"
                    )
                except Exception:
                    pass
            else:
                await update.message.reply_text("❌ Foydalanuvchi topilmadi!")
            db.close()
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID format! Iltimos faqat raqam yuboring.")
        
        context.user_data.pop('admin_action', None)
        await admin_panel(update, context)
        return MENU
        
    elif action == 'waiting_revoke':
        try:
            target_id = int(update.message.text)
            db = SessionLocal()
            user = db.query(User).filter(User.id == target_id).first()
            
            if user:
                user.full_access = False
                db.commit()
                await update.message.reply_text(f"✅ {target_id} ning cheksiz huquqi olib tashlandi!")
            else:
                await update.message.reply_text("❌ Foydalanuvchi topilmadi!")
            db.close()
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID format! Iltimos faqat raqam yuboring.")
        
        context.user_data.pop('admin_action', None)
        await admin_panel(update, context)
        return MENU
    
    return MENU

# ==================== MAIN ====================
def main():
    """Start the bot"""
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    # Create conversation handler
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
    
    # Add conversation handler
    application.add_handler(conv)
    
    # Start the bot
    print("🤖 Bot ishga tushdi...")
    print(f"Admin ID: {ADMIN_ID}")
    print("Press Ctrl+C to stop")
    application.run_polling()

if __name__ == '__main__':
    main()

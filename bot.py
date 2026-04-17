import logging
import random
import io
from sqlalchemy import create_engine, Column, Integer, String, Float, BigInteger, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
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
REQUIRED_CHANNELS = ["@misbah_ilm"] # Must be a public channel username
ADMIN_ID = int(os.getenv("ADMIN_ID", "647129875")) # Your Telegram ID

# --- DB SETUP ---
Base = declarative_base()

engine = create_engine(
    DATABASE_URL,
    pool_size=1,
    max_overflow=0,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    full_name = Column(String)
    major = Column(String) # [cite: 7, 8]
    phone = Column(String)
    points = Column(Float, default=3.0) # 
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
    correct = Column(String) # 'a', 'b', 'c', or 'd'

Base.metadata.create_all(engine)

# --- STATES ---
NAME, MAJOR, PHONE, MENU, TESTING, CHANGE_MAJOR, ADMIN_ADD_Q = range(7)

# --- LOGIC ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id
        
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()

    # Subscription Check
    not_subscribed = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(ch, user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(ch)
        except Exception as e:
            print(f"Error checking channel {ch}: {e}")
            not_subscribed.append(ch)

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
        if not query and context.args: context.user_data['ref'] = context.args[0]
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
        btns = [['Hamshiralik ishi', 'Akusherlik ishi'], ['Patronaj hamshira', 'Davolash ishi']] # [cite: 8]
        await update.message.reply_text("Yo'nalishni tanlang:", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True))
        context.user_data['reg_state'] = MAJOR
        return MAJOR

    elif state == MAJOR:
        context.user_data['major'] = update.message.text
        btn = [[KeyboardButton("Raqamni yuborish", request_contact=True)]] # [cite: 5, 15]
        await update.message.reply_text("Telefon raqamingizni yuboring:", reply_markup=ReplyKeyboardMarkup(btn, resize_keyboard=True))
        context.user_data['reg_state'] = PHONE
        return PHONE

async def save_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = SessionLocal()
    
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
        
        # Referral Logic 
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
                except Exception as e:
                    print(f"Invite notification error: {e}")
                
        db.commit()
    finally:
        db.close()
    await update.message.reply_text("Ro'yxatdan o'tdingiz! 3 ball va 3 urinish berildi.", reply_markup=main_menu())
    return MENU

async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == update.effective_user.id).first()
        
        if not user.full_access and user.attempts <= 0:
            db.close()
            await update.message.reply_text(
                "👀Afsuski siz barcha test ishlash imkoniyatlaringizni ishlatib bo'libsiz.\n"
                "🔥 Botga yana ko'proq yaqinlaringizni taklif qilib, shuncha ko'p tayyorgarlik imkoniyatingizni oshiring.\n"
                "Har bitta hamshira tanishingizni referal havolangiz orqali loyihaga taklif qilsangiz +3 ta trenirovka imkoniyatini olasiz.\n"
                "Bilimingizni oshirishdadavom eting! 👏 😊\n",
                reply_markup=main_menu()
            )
            return await extra_options(update, context)
        
        questions = db.query(Question).filter(Question.major == user.major).all()
        if len(questions) < 25:
            db.close()
            await update.message.reply_text("Kechirasiz, bazada yetarli savollar yo'q.")
            return MENU
        
        selected = random.sample(questions, 25)
        if not user.full_access:
            user.attempts -= 1
        db.commit()
    finally:
        db.close()
    
    q_list = []
    for q in selected:
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
            for i, (orig_key, text) in enumerate(options):
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
        # Final Score: correct = +1, wrong = 0
        c = context.user_data['correct_count']
        w = 25 - c
        score = float(c)  # 1 point per correct answer, 0 for wrong
        
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
        except Exception as e:
            print(f"Invite friends error: {e}")
            await query.message.reply_text("Kechirasiz, xatolik yuz berdi. Iltimos keyinroq urinib ko'ring.")
    elif query.data == 'buy_attempts':
        await query.message.reply_text("Qo'shimcha urinish sotib olish uchun admin bilan bog'laning: @AzizJurayev")
    return MENU

async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Bu yerda admin logini (masalan: @sizning_loginingiz) ni yozishingiz mumkin
    await update.message.reply_text("Admin bilan bog'lanish uchun shu manzilga murojaat qiling: @AzizJurayev")
    return MENU

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "Admin Paneli 🛠\n\nYangi test qo'shish uchun quyidagi formatda yuboring:\n\n"
        "Yo'nalish nomi (Misol: Hamshiralik ishi)\n"
        "Savol matni\n"
        "A javob\n"
        "B javob\n"
        "C javob\n"
        "D javob\n"
        "a  (<- faqat to'g'ri javob harfi)\n\n"
        "Bekor qilish uchun /cancel yozing."
    )
    return ADMIN_ADD_Q

async def admin_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    db = SessionLocal()
    from sqlalchemy import func
    stats = db.query(Question.major, func.count(Question.id)).group_by(Question.major).all()
    
    if not stats:
        await update.message.reply_text("Bazada hali hech qanday savol yo'q.")
        return
        
    msg = "📊 Bazadagi savollar soni:\n\n"
    for major, count in stats:
        msg += f"🔸 {major}: {count} ta\n"
        
    await update.message.reply_text(msg)

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
    
    await update.message.reply_text(f"✅ Test muvaffaqiyatli qo'shildi ({major})!\n\nYana test qo'shishingiz yoki /cancel yozishingiz mumkin.")
    return ADMIN_ADD_Q

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    if not context.args:
        await update.message.reply_text("Foydalanish: /broadcast <xabar matni>")
        return
        
    message_text = update.message.text.split(' ', 1)[1]
    
    db = SessionLocal()
    users = db.query(User.id).all()
    db.close()
    
    success = 0
    failed = 0
    result_msg = await update.message.reply_text("Xabarni yuborish boshlandi, biroz kuting...")
    
    for (uid,) in users:
        try:
            await context.bot.send_message(chat_id=uid, text=message_text)
            success += 1
        except Exception:
            failed += 1
            
    await result_msg.edit_text(f"Xabar yuborish yakunlandi!\n✅ Muvaffaqiyatli: {success}\n❌ Xato/Bloklangan: {failed}")


async def grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    if not context.args:
        await update.message.reply_text("Foydalanish: /grant_access <foydalanuvchi_id>")
        return
        
    target_id = context.args[0]
    if not target_id.isdigit():
        await update.message.reply_text("Foydalanuvchi ID si faqattan raqamlardan iborat bo'lishi kerak!")
        return
        
    target_id = int(target_id)
    db = SessionLocal()
    user = db.query(User).filter(User.id == target_id).first()
    
    if not user:
        await update.message.reply_text("Bunday ID ga ega foydalanuvchi topilmadi!")
        db.close()
        return
        
    user.full_access = True
    db.commit()
    db.close()
    
    await update.message.reply_text(f"Foydalanuvchi {target_id} ga to'liq kirish huquqi (cheksiz urinish) berildi!")
    try:
        await context.bot.send_message(chat_id=target_id, text="🌟 Tabriklaymiz! Sizga admin tomonidan testlarni cheksiz ishlash huquqi berildi!")
    except Exception:
        pass

async def revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    if not context.args:
        await update.message.reply_text("Foydalanish: /revoke_access <foydalanuvchi_id>")
        return
        
    target_id = context.args[0]
    if not target_id.isdigit():
        return
        
    target_id = int(target_id)
    db = SessionLocal()
    user = db.query(User).filter(User.id == target_id).first()
    
    if user:
        user.full_access = False
        db.commit()
        await update.message.reply_text(f"Foydalanuvchi {target_id} ning cheksiz huquqi bekor qilindi.")
    else:
        await update.message.reply_text("Bunday ID topilmadi.")
    db.close()

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    
    if not users:
        await update.message.reply_text("Bazada hech qanday foydalanuvchi yo'q.")
        return
        
    text_content = "ID | Ism | Telefon | Yo'nalish | Cheksiz_huquq\n"
    text_content += "-"*60 + "\n"
    for u in users:
        access_str = "Ha" if u.full_access else "Yo'q"
        text_content += f"{u.id} | {u.full_name} | {u.phone} | {u.major} | {access_str}\n"
        
    doc = io.BytesIO(text_content.encode('utf-8'))
    doc.name = "users.txt"
    await context.bot.send_document(chat_id=update.effective_user.id, document=doc, caption="Barcha foydalanuvchilar ro'yxati:")

def main_menu():
    return ReplyKeyboardMarkup([
        ['📝 Testni boshlash'], 
        ['📊 Mening statistikam'], 
        ["✨ Qo'shimcha imkoniyatlar"],
        ['👨‍💻 Bog\'lanish']
    ], resize_keyboard=True)

def main():
    app = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start), 
            CommandHandler('admin', admin_panel), 
            CommandHandler('info', admin_info),
            CommandHandler('broadcast', admin_broadcast),
            CommandHandler('grant_access', grant_access),
            CommandHandler('revoke_access', revoke_access),
            CallbackQueryHandler(start, pattern='^check_sub$')
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
                CommandHandler('admin', admin_panel),
                CommandHandler('info', admin_info),
                CommandHandler('broadcast', admin_broadcast),
                CommandHandler('grant_access', grant_access),
                CommandHandler('revoke_access', revoke_access),
                CommandHandler('users', admin_users)
            ],
            CHANGE_MAJOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_major)
            ],
            ADMIN_ADD_Q: [
                MessageHandler(filters.TEXT, admin_save_question)
            ],
            TESTING: [PollAnswerHandler(handle_answer)]
        },
        fallbacks=[
            CommandHandler('start', start), 
            CommandHandler('admin', admin_panel), 
            CommandHandler('info', admin_info),
            CommandHandler('broadcast', admin_broadcast),
            CommandHandler('grant_access', grant_access),
            CommandHandler('revoke_access', revoke_access),
            CallbackQueryHandler(start, pattern='^check_sub$')
        ],
        per_chat=False
    )
    
    app.add_handler(conv)
    app.run_polling()

if __name__ == '__main__':
    main()
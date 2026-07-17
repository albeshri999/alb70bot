# -*- coding: utf-8 -*-
import logging
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from config import BOT_TOKEN
from storage import migrate_days
from handlers import (
    start, handle_message, handle_day_selection, participants, unlock,
    handle_menu_competition, handle_menu_credit, handle_menu_balance,
    handle_menu_leaderboard, handle_word_next,
    handle_hint, handle_back_to_main, handle_back_to_days,
    handle_notif_user, handle_notif_tlog, handle_notif_results,
)
from admin import build_admin_handler
from quiz_admin import build_quiz_admin_handler
from admin_settings import build_admin_settings_handler
from quiz_user import (
    handle_menu_quizzes, handle_quiz_view, handle_quiz_start, handle_quiz_answer,
)
from admin_management import add_admin_cmd, remove_admin_cmd, list_admins_cmd
from distro_admin import build_distro_admin_handler
from distro_user import (
    handle_menu_distro, handle_distro_view, handle_distro_start, handle_distro_answer,
)
from initiatives_admin import build_initiatives_admin_handler
from initiatives_user import (
    handle_menu_initiatives, handle_initiative_request, handle_initiative_noop,
    handle_menu_my_initiatives,
)
from achievements_user import handle_menu_achievements
from submissions_admin import (
    build_submissions_admin_handler,
    chsb_score, chsb_scoreeditopen, chsb_scoreback, chsb_scoreval, chsb_scoredel,
    chsb_approve, chsb_approve_yes,
    chsb_reject, chsb_reject_yes,
    chsb_delete, chsb_delete_yes,
    chsb_cancel,
)
from server_admin import build_server_admin_handler
from submissions_user import (
    handle_menu_submissions, handle_submission_view, handle_submission_start,
    handle_submission_media,
)
from words_admin import build_words_admin_handler
from words_user import handle_menu_words, handle_words_day_pick

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is missing.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Notification button handlers — registered first so they always fire
    # regardless of admin ConversationHandler state
    app.add_handler(CallbackQueryHandler(handle_notif_user,    pattern=r"^notif_user_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_notif_tlog,    pattern=r"^notif_tlog_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_notif_results, pattern=r"^notif_results_\d+$"))

    # Submissions channel moderation buttons (⭐/🏆/❌/🗑 on each channel post)
    # — registered early since they're attached to channel messages, entirely
    # outside any ConversationHandler's per-chat state.
    app.add_handler(CallbackQueryHandler(chsb_score,   pattern=r"^chsb_score_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_scoreeditopen, pattern=r"^chsb_scoreeditopen_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_scoreback,     pattern=r"^chsb_scoreback_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_scoreval,      pattern=r"^chsb_scoreval_\d+_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_scoredel,      pattern=r"^chsb_scoredel_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_approve, pattern=r"^chsb_approve_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_approve_yes, pattern=r"^chsb_approve_yes_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_reject,  pattern=r"^chsb_reject_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_reject_yes, pattern=r"^chsb_reject_yes_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_delete,  pattern=r"^chsb_delete_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_delete_yes, pattern=r"^chsb_delete_yes_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(chsb_cancel,  pattern=r"^chsb_cancel_\d+_\d+$"))

    # Admin ConversationHandler
    app.add_handler(build_admin_handler())

    # Quiz Management — fully independent ConversationHandler (📝 إدارة الاختبارات)
    app.add_handler(build_quiz_admin_handler())

    # Admin Settings — owner-only ConversationHandler (⚙️ إعدادات المشرفين)
    app.add_handler(build_admin_settings_handler())

    # Team-Distribution Test — fully independent ConversationHandler
    # (👥 اختبار توزيع الفرق). No points/leaderboard interaction whatsoever.
    app.add_handler(build_distro_admin_handler())

    # Initiatives — fully independent ConversationHandler (💡 إدارة المبادرات)
    app.add_handler(build_initiatives_admin_handler())

    # Submissions — fully independent ConversationHandler (🎭 إدارة المشاركات)
    app.add_handler(build_submissions_admin_handler())

    # Server management — fully independent ConversationHandler, ADMIN_ID only
    # (🖥 إدارة السيرفر)
    app.add_handler(build_server_admin_handler())

    # Words — fully independent ConversationHandler (📖 إدارة الكلمات)
    app.add_handler(build_words_admin_handler())

    # Regular user commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("participants", participants))
    app.add_handler(CommandHandler("unlock", unlock))

    # Multi-admin management (owner-only add/remove, any-admin list)
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
    app.add_handler(CommandHandler("admins", list_admins_cmd))

    # Main menu buttons
    app.add_handler(CallbackQueryHandler(handle_menu_competition, pattern="^menu_competition$"))
    app.add_handler(CallbackQueryHandler(handle_menu_credit,      pattern="^menu_credit$"))
    app.add_handler(CallbackQueryHandler(handle_menu_balance,     pattern="^menu_balance$"))
    app.add_handler(CallbackQueryHandler(handle_menu_leaderboard, pattern="^menu_leaderboard$"))

    # Quiz Management — participant-facing (📝 الاختبارات), fully independent
    app.add_handler(CallbackQueryHandler(handle_menu_quizzes, pattern="^menu_quizzes$"))
    app.add_handler(CallbackQueryHandler(handle_quiz_view,    pattern=r"^qz_view_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_quiz_start,   pattern=r"^qz_start_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_quiz_answer,  pattern=r"^qz_ans_\d$"))

    # Team-Distribution Test — participant-facing (👥 اختبار تنظيمي), fully
    # independent of the quiz system above and never touches credits/leaderboard.
    app.add_handler(CallbackQueryHandler(handle_menu_distro,   pattern="^menu_distro$"))
    app.add_handler(CallbackQueryHandler(handle_distro_view,   pattern=r"^dz_view_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_distro_start,  pattern=r"^dz_start_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_distro_answer, pattern=r"^dz_ans_\d$"))

    # Initiatives — participant-facing (💡 فرص المبادرات), fully independent
    app.add_handler(CallbackQueryHandler(handle_menu_initiatives,   pattern="^menu_initiatives$"))
    app.add_handler(CallbackQueryHandler(handle_initiative_request, pattern=r"^in_req_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_initiative_noop,    pattern="^in_noop$"))
    app.add_handler(CallbackQueryHandler(handle_menu_my_initiatives, pattern="^menu_my_initiatives$"))

    # Achievements — participant-facing (🏅 إنجازاتي), fully independent
    app.add_handler(CallbackQueryHandler(handle_menu_achievements, pattern="^menu_achievements$"))

    # Submissions — participant-facing (🎭 المشاركات), fully independent
    app.add_handler(CallbackQueryHandler(handle_menu_submissions,  pattern="^menu_submissions$"))
    app.add_handler(CallbackQueryHandler(handle_submission_view,   pattern=r"^sb_view_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_submission_start,  pattern=r"^sb_submit_\w+$"))

    # Words — participant-facing (🎤 إلقاء الكلمات), fully independent
    app.add_handler(CallbackQueryHandler(handle_menu_words,     pattern="^menu_words$"))
    app.add_handler(CallbackQueryHandler(handle_words_day_pick, pattern=r"^wduser_day_\w+$"))

    # Hint button
    app.add_handler(CallbackQueryHandler(handle_hint, pattern="^hint_reveal$"))

    # Next-word button (after a word is already completed)
    app.add_handler(CallbackQueryHandler(handle_word_next, pattern="^word_next$"))

    # Back navigation
    app.add_handler(CallbackQueryHandler(handle_back_to_main, pattern="^back_to_main$"))
    app.add_handler(CallbackQueryHandler(handle_back_to_days, pattern="^back_to_days$"))

    # Day selection
    app.add_handler(CallbackQueryHandler(handle_day_selection, pattern=r"^day_\d+$"))

    # Submissions — file uploads (🎤 صوت / 🎥 فيديو / 📷 صورة). Strict no-op
    # unless the sending user is currently expected to upload for a specific
    # submission (see submissions_user.PENDING_KEY) — never interferes with
    # any other message flow in the bot.
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.VIDEO | filters.PHOTO,
        handle_submission_media,
    ))

    # General text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    migrate_days()   # one-time migration: passwords/prompts → stages
    logger.info("Bot is starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

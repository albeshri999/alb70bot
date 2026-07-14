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

    # Admin ConversationHandler
    app.add_handler(build_admin_handler())

    # Quiz Management — fully independent ConversationHandler (📝 إدارة الاختبارات)
    app.add_handler(build_quiz_admin_handler())

    # Admin Settings — owner-only ConversationHandler (⚙️ إعدادات المشرفين)
    app.add_handler(build_admin_settings_handler())

    # Team-Distribution Test — fully independent ConversationHandler
    # (👥 اختبار توزيع الفرق). No points/leaderboard interaction whatsoever.
    app.add_handler(build_distro_admin_handler())

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
    app.add_handler(CallbackQueryHandler(handle_quiz_answer,  pattern=r"^qz_ans_(a|b)$"))

    # Team-Distribution Test — participant-facing (👥 اختبار تنظيمي), fully
    # independent of the quiz system above and never touches credits/leaderboard.
    app.add_handler(CallbackQueryHandler(handle_menu_distro,   pattern="^menu_distro$"))
    app.add_handler(CallbackQueryHandler(handle_distro_view,   pattern=r"^dz_view_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_distro_start,  pattern=r"^dz_start_\w+$"))
    app.add_handler(CallbackQueryHandler(handle_distro_answer, pattern=r"^dz_ans_(a|b)$"))

    # Hint button
    app.add_handler(CallbackQueryHandler(handle_hint, pattern="^hint_reveal$"))

    # Next-word button (after a word is already completed)
    app.add_handler(CallbackQueryHandler(handle_word_next, pattern="^word_next$"))

    # Back navigation
    app.add_handler(CallbackQueryHandler(handle_back_to_main, pattern="^back_to_main$"))
    app.add_handler(CallbackQueryHandler(handle_back_to_days, pattern="^back_to_days$"))

    # Day selection
    app.add_handler(CallbackQueryHandler(handle_day_selection, pattern=r"^day_\d+$"))

    # General text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    migrate_days()   # one-time migration: passwords/prompts → stages
    logger.info("Bot is starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

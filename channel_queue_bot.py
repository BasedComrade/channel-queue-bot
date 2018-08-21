import datetime
import datetime
import json
import logging
import os
import sys
from functools import wraps
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, TelegramError, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler

import channel_instance_handler

# setup logger
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


def import_config():
    dir = os.path.dirname(__file__)
    path = os.path.join(dir, 'config.json')
    with open(path) as data_file:
        data = json.load(data_file)
    global config
    config = data


config = {}
channel_handlers = {}
updater = None


def needs_focus(func):
    @wraps(func)
    def wrapped(bot, update, *args, **kwargs):
        user_id = update.message.chat_id
        if str(user_id) not in config['focus_channels']:
            bot.send_message(chat_id=user_id,
                             text="You are not currently working with a channel. Use /select to start working with a channel.")
            return
        channel_handler = channel_handlers[str(config['focus_channels'][str(user_id)])]
        if user_id not in channel_handler.config['admins']:
            bot.send_message(chat_id=user_id, text="You are not an admin in %s!" % channel_handler.chat.title)
            return
        return func(bot, update, channel_handler, *args, **kwargs)

    return wrapped


def needs_focus_args(func):
    @wraps(func)
    def wrapped(bot, update, *args, **kwargs):
        user_id = update.message.chat_id
        if str(user_id) not in config['focus_channels']:
            bot.send_message(chat_id=user_id,
                             text="You are not currently working with a channel. Use /select to start working with a channel.")
            return
        channel_handler = channel_handlers[str(config['focus_channels'][str(user_id)])]
        if user_id not in channel_handler.config['admins']:
            bot.send_message(chat_id=user_id, text="You are not an admin in %s!" % channel_handler.chat.title)
            return
        return func(bot, update, channel_handler, *args, **kwargs)

    return wrapped


def get_config():
    return config


def main():
    import_config()

    global updater
    updater = Updater(config['token'])
    dispatcher = updater.dispatcher

    # register error handler
    dispatcher.add_error_handler(error)

    # start handlers for each channel in config
    register_channel_handlers()

    # register commands
    dispatcher.add_handler(CommandHandler('addchannel', add_channel))
    dispatcher.add_handler(CommandHandler('cancel', cancel_process))
    dispatcher.add_handler(CommandHandler('select', select_channel))
    dispatcher.add_handler(CommandHandler('dump', dump_data))
    dispatcher.add_handler(CommandHandler('shuffle', shuffle_queue))
    dispatcher.add_handler(CommandHandler('focus', focus_command))
    dispatcher.add_handler(CommandHandler('queue', queue_command))
    dispatcher.add_handler(CommandHandler('timezone', select_timezone))
    dispatcher.add_handler(CommandHandler('times', times))
    dispatcher.add_handler(CommandHandler(['addtime', 'addtimes'], add_time, pass_args=True))
    dispatcher.add_handler(CommandHandler(['removetime', 'removetimes'], remove_time, pass_args=True))

    # register message listeners
    dispatcher.add_handler(MessageHandler(~ Filters.command, message_received))
    dispatcher.add_handler(MessageHandler(Filters.command, unknown_command))

    # register button handlers
    dispatcher.add_handler(CallbackQueryHandler(remove_post, pattern="remove"))
    dispatcher.add_handler(CallbackQueryHandler(select_timezone, pattern="select_timezone"))
    dispatcher.add_handler(CallbackQueryHandler(select_timezone, pattern="set_timezone"))

    # start loops
    updater.job_queue.run_repeating(dump_data, 900)
    updater.job_queue.run_repeating(update_admins, 900)

    # start the bot
    updater.start_polling()
    updater.idle()


def register_channel_handlers():
    global channel_handlers
    for channel_id in config['channels']:
        channel_handlers[channel_id] = channel_instance_handler.ChannelInstanceHandler(updater, channel_id, config)


def start(bot, update):
    pass


@needs_focus
def queue_command(bot, update, focus_channel):
    user_id = update.message.from_user.id
    bot.send_message(chat_id=update.message.chat_id, text="There are currently *%d* posts queued for *%s*" % (
    len(focus_channel.queue), focus_channel.chat.title), parse_mode='Markdown')


def select_timezone(bot, update):
    global config
    index = 0
    if update.callback_query is not None:
        query = update.callback_query
        args = query.data.split(':')
        if args[0] == "select_timezone":
            index = int(args[1])
        else:
            user_id = query.from_user.id
            timezone = config['timezones'][int(args[1])]
            config['timezone_prefs'][str(user_id)] = int(timezone)
            query.edit_message_text(text="Your time zone has been set to *UTC%s*." % timezone, parse_mode='Markdown',
                                    reply_markup=None)
            query.answer()
            return
    text = "Use the arrow buttons to select your time zone relative to UTC.\n\n"
    now = datetime.datetime.utcnow()
    hour = now.hour
    minute = now.minute
    hour += int(config['timezones'][index])
    if hour < 0:
        hour += 24
    elif hour > 23:
        hour -= 24
    if hour > 12:
        twelve_hour = hour - 12
        suffix = "pm"
    elif hour == 12:
        twelve_hour = 12
        suffix = 'pm'
    elif hour == 0:
        twelve_hour = 12
        suffix = 'am'
    else:
        twelve_hour = hour
        suffix = 'am'
    if hour > 12:
        time = datetime.time(hour, minute)
        full_time = time.strftime(" (%H:%M)")
    else:
        full_time = ""
    time = datetime.time(twelve_hour, minute)
    time_line = time.strftime("%H:%M")
    text += "It is currently *%s%s*%s" % (time_line, suffix, full_time)
    lower = index - 1
    if lower < 0:
        lower = len(config['timezones']) - 1
    upper = index + 1
    if upper == len(config['timezones']):
        upper = 0
    select_label = "Select UTC" + config['timezones'][index]
    keyboard = [[InlineKeyboardButton("⬅", callback_data="select_timezone:%d" % lower),
                 InlineKeyboardButton("➡", callback_data="select_timezone:%d" % upper)],
                [InlineKeyboardButton(select_label, callback_data="set_timezone:%d" % index)]]
    markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query is None:
        bot.send_message(chat_id=update.message.chat_id, text=text, parse_mode='Markdown', reply_markup=markup)
    else:
        update.callback_query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=markup)
        update.callback_query.answer()


@needs_focus
def times(bot, update, focus_channel):
    focus_channel.times(bot, update)


@needs_focus_args
def add_time(bot, update, focus_channel, args):
    focus_channel.add_time(bot, update, args)


@needs_focus_args
def remove_time(bot, update, focus_channel, args):
    focus_channel.remove_time(bot, update, args)


@needs_focus
def shuffle_queue(bot, update, focus_channel):
    focus_channel.shuffle(bot, update)


def cancel_process(bot, update):
    user_id = update.message.chat_id
    global config
    if user_id in config['waiting_for_channel_setup']:
        config['waiting_for_channel_setup'].remove(user_id)
        bot.send_message(chat_id=user_id, text="Ok, I've canceled the setup process for a new channel.")
        return
    if user_id in config['waiting_for_channel_select']:
        config['waiting_for_channel_select'].remove(user_id)
        bot.send_message(chat_id=user_id, text="Ok, I've canceled selecting a channel.")
        return
    bot.send_message(chat_id=user_id, text="Were you doing something?")


def focus_command(bot, update):
    user_id = update.message.chat_id
    if str(user_id) not in config['focus_channels']:
        bot.send_message(chat_id=user_id,
                         text="You are not currently working with any channels. Use /select to start working with a registered channel.")
        return
    focus_title = channel_handlers[str(config['focus_channels'][str(user_id)])].chat.title
    bot.send_message(chat_id=user_id,
                     text="*You are currently working with %s.*\n\nAny messages you send to me will be queued "
                          "for %s and any channel-specific commands will also target %s.\n\nYou can "
                          "use /select to switch to another channel." % (focus_title, focus_title, focus_title),
                     parse_mode='Markdown')


def add_channel(bot, update):
    global config
    user_id = update.message.chat_id
    if user_id in config['waiting_for_channel_setup']:
        bot.send_message(chat_id=user_id,
                         text="You are already in the process of setting up a channel. Forward a from the target channel to me or use /cancel to cancel the setup process.")
        return
    bot.send_message(chat_id=user_id,
                     text="Ok, let's set up a queue for a channel. Forward a message from the target channel to me.")
    config['waiting_for_channel_setup'].append(user_id)


def setup_channel(bot, update):
    global config
    user_id = update.message.chat_id

    # assure message is forwarded from channel
    if update.message.forward_from_chat is None:
        bot.send_message(chat_id=user_id, text="You need to forward a message from the target channel to me!")
        return
    target_chat = update.message.forward_from_chat
    if target_chat.type != 'channel':
        bot.send_message(chat_id=user_id, text="I only support *channels*!", parse_mode='Markdown')
        bot.send_message(chat_id=user_id,
                         text="Forward a message from the target channel to me or use /cancel to cancel the setup process.")
        return

    # assure bot is admin in target channel
    try:
        admins = target_chat.get_administrators()
    except TelegramError:
        bot.send_message(chat_id=user_id,
                         text="You need to add me as an admin in %s to establish a queue." % target_chat.title)
        config['waiting_for_channel_setup'].remove(user_id)
        return

    # assure user is channel creator
    chat_member = None
    for admin in target_chat.get_administrators():
        if admin.user.id == user_id:
            chat_member = admin
    if chat_member is None or chat_member.status != 'creator':
        bot.send_message(chat_id=user_id, text="Only the channel creator can set up a queue for a channel!")
        config['waiting_for_channel_setup'].remove(user_id)
        return

    # assure channel isn't already registered
    if str(target_chat.id) in config['channels']:
        bot.send_message(chat_id=user_id,
                         text="Good news! A queue for that channel has already been setup. Use /select to start working with it.")
        config['waiting_for_channel_setup'].remove(user_id)
        return

    # register and store new channel instance handler
    global channel_handlers
    new_handler = channel_instance_handler.ChannelInstanceHandler(updater, target_chat.id, config)
    channel_handlers[str(target_chat.id)] = new_handler

    bot.send_message(chat_id=user_id,
                     text="I've successfully set up a queue for *%s*! Use /select to start working with it." % target_chat.title,
                     parse_mode='Markdown')
    config['waiting_for_channel_setup'].remove(user_id)


def select_channel(bot, update):
    user_id = update.message.chat_id
    available_channels = []
    for channel in channel_handlers.values():
        if user_id in channel.config['admins']:
            available_channels.append(channel)
    if len(available_channels) == 0:
        bot.send_message(chat_id=user_id, text="You are not an admin in any registered channels!")
        return
    channel_titles = [channel.chat.title for channel in available_channels]
    duplicates = [x for x in channel_titles if channel_titles.count(x) > 1]
    for index, channel in enumerate(available_channels):
        if channel.chat.title in duplicates:
            channel_titles[index] = "%s (%d)" % (channel.chat.title, channel.chat.id)
    keyboard = [[]]
    for title in channel_titles:
        keyboard[0].append(title)
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    bot.send_message(chat_id=user_id, text="Select which channel you want to work with.", reply_markup=markup)
    global config
    config['waiting_for_channel_select'].append(user_id)


def select_channel_reply(bot, update):
    channel_title = update.message.text
    user_id = update.message.chat_id
    global config
    config['waiting_for_channel_select'].remove(user_id)

    # make sure response is a text message
    if update.message.text is None:
        bot.send_message(chat_id=update.message.chat_id, text="Invalid selection.", reply_markup=ReplyKeyboardRemove())
        return

    # check if title has an id identifier at end
    channel_id = None
    id_identifier = channel_title.split(' ')[-1]
    if id_identifier[0] == '(' and id_identifier[-1] == ')':
        try:
            channel_id = int(id_identifier[1:-2])
        except ValueError:
            pass

    selected_channel = None
    for channel in channel_handlers.values():
        if channel.chat.title.lower() == channel_title.lower():
            if channel_id is not None:
                if channel.chat.id == channel_id:
                    selected_channel = channel
                else:
                    continue
            else:
                selected_channel = channel

    if selected_channel is None:
        if channel_id is None:
            bot.send_message(chat_id=user_id, text="There is no registered channel named %s." % channel_title,
                             reply_markup=ReplyKeyboardRemove())
        else:
            bot.send_message(chat_id=user_id,
                             text="There are more than one registered channel named %s!" % channel_title,
                             reply_markup=ReplyKeyboardRemove())
        return

    # make sure user is admin in selected channel
    if user_id not in [admin.user.id for admin in selected_channel.chat.get_administrators()]:
        bot.send_message(chat_id=user_id, text="You are not an admin in %s!" % channel_title,
                         reply_markup=ReplyKeyboardRemove)
        return

    config['focus_channels'][str(user_id)] = selected_channel.chat.id
    bot.send_message(chat_id=user_id,
                     text="*You are now working with %s.*\n\nAny messages you send to me will be queued "
                          "for %s and any channel-specific commands will also target %s." % (
                              selected_channel.chat.title, selected_channel.chat.title, selected_channel.chat.title),
                     parse_mode='Markdown',
                     reply_markup=ReplyKeyboardRemove())
    return


def message_received(bot, update):
    user_id = update.message.from_user.id
    if user_id in config['waiting_for_channel_setup']:
        setup_channel(bot, update)
        return
    if user_id in config['waiting_for_channel_select']:
        select_channel_reply(bot, update)
        return
    add_content(bot, update)


@needs_focus
def add_content(bot, update, focus_channel):
    message = update.message
    if message.text is not None:
        focus_channel.add_text(bot, update)
    elif any((message.photo, message.video, message.audio, message.document, message.sticker, message.voice,
              message.video_note)):
        focus_channel.add_media(bot, update)
    else:
        bot.send_message(chat_id=message.chat_id, text="Unsupported content type.")


def remove_post(bot, update):
    query = update.callback_query
    args = query.data.split('[&sp?]')
    post_id = int(args[2])
    target_channel = channel_handlers[args[1]]
    target_channel.remove_post(bot, update, post_id)


def restart_bot(bot, update):
    if update.message.from_user.id in config['admins']:
        bot.send_message(chat_id=update.message.chat_id, text="Restarting bot...")
        dump_data()
        os.execl(sys.executable, sys.executable, *sys.argv)


def unknown_command(bot, update):
    bot.send_message(chat_id=update.message.chat_id, text="I didn't recognize that command!")


def dump_data(bot=None, job=None, update=None):
    for channel_handler in channel_handlers.values():
        channel_handler.dump_data()
    data = json.dumps(config)
    dir = os.path.dirname(__file__)
    path = os.path.join(dir, 'config.json')
    with open(path, "w") as f:
        f.write(data)


def update_admins(bot=None, job=None):
    for handler in channel_handlers.values():
        handler.update_admins()


# logs bot errors thrown
def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"' % (update, error))


if __name__ == '__main__':
    main()

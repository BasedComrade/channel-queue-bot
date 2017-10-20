import datetime
import json
import logging
import os
from threading import Thread

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

# setup logger
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

g_config = {}
file_dir = __file__


class ChannelQueue(Thread):
    def __init__(self, token):
        Thread.__init__(self)
        self.config = g_config[token]
        self.token = token
        self.should_end = False

    def run(self):
        self.updater = Updater(self.token)
        self.dispatcher = self.updater.dispatcher
        self.bot = self.updater.bot

        # register error handler
        self.dispatcher.add_error_handler(error)

        # connect target channel
        self.connect_channel()
        if self.should_end:
            return

        # set up variables
        self.load_queue()

        # set up commands
        self.dispatcher.add_handler(CommandHandler('dump', self.dump_data))
        self.dispatcher.add_handler(CommandHandler('send', self.send_command))
        self.dispatcher.add_handler(CommandHandler('time', self.get_time))
        self.dispatcher.add_handler(CommandHandler('queue', self.queue_count))

        # set up add content handlers
        self.dispatcher.add_handler(MessageHandler(Filters.text, self.add_text))
        self.dispatcher.add_handler(MessageHandler(
            Filters.photo | Filters.video | Filters.audio | Filters.document | Filters.sticker | Filters.voice,
            self.add_media))

        # set up post loops
        self.start_post_loops()

        # start the bot
        self.updater.start_polling()

        self.updater.idle(stop_signals=())

    def connect_channel(self):
        channel_id = int(self.config['channel_id'])
        self.chat = self.bot.get_chat(chat_id=channel_id)
        self.bot_name = "%s Queue Bot" % self.chat.title
        if self.chat.type != 'channel':
            logger.warning("Channel ID points to a chat that isn't a channel\nExiting.")
            self.should_end = True
        if self.chat.get_member(self.bot.get_me().id) is None:
            logger.warning("Not a member of %s" % self.chat.title)

    def start_post_loops(self):
        times = self.config['post_times']
        for time_string in times:
            args = time_string.split(':')
            hours = int(args[0])
            minutes = int(args[1])
            time = datetime.time(hours, minutes)
            self.updater.job_queue.run_daily(self.push_post, time)

    def load_queue(self):
        self.current_index = 0
        self.reference_index = -1
        read_queue = self.config['queued_posts']
        self.queue = ["null"] * len(read_queue)
        for message_data in read_queue:
            index = int(message_data.split(':')[0])
            if self.reference_index == -1:
                self.reference_index = index
            else:
                self.reference_index = min(self.reference_index, index)
        for message_data in read_queue:
            index = int(message_data.split(':')[0])
            self.queue[index - self.reference_index] = message_data

    def add_text(self, bot, update):
        if not self.is_admin(update):
            return
        if len(self.queue) == 0:
            next_index = 1
        else:
            next_index = int(self.queue[-1].split(":")[0]) + 1
        text = update.message.text_markdown
        text = text.replace(':', '&cl')
        data_text = "%d:text:%s" % (next_index, text)
        self.queue.append(data_text)

    def add_media(self, bot, update):
        if not self.is_admin(update):
            return
        message = update.message
        type = None
        if message.photo is not None:
            type = "photo"
        elif message.video is not None:
            type = "video"
        elif message.audio is not None:
            type = "audio"
        elif message.document is not None:
            type = "document"
        elif message.sticker is not None:
            type = "sticker"
        elif message.voice is not None:
            type = "voice"
        else:
            type = "video_note"
        if len(self.queue) == 0:
            next_index = 1
        else:
            next_index = int(self.queue[-1].split(":")[0]) + 1
        file_id = message.photo[-1].file_id
        caption = message.caption
        data_text = "%d:%s:%s" % (next_index, type, file_id)
        if caption is not None:
            data_text += ":%s" % message.caption.replace(':', '&cl')
        self.queue.append(data_text)

    def send_post(self, data_text):
        disable_notifications = self.config['disable_notifications']
        args = data_text.split(':')
        type = args[1]
        chat_id = self.chat.id
        if type == 'text':
            message_text = args[2].replace('&cl', ':')
            self.bot.send_message(chat_id=chat_id, text=message_text, parse_mode='Markdown',
                                  disable_notification=disable_notifications)
            return
        file_id = self.bot.get_file(args[2]).file_id
        if type in ('audio', 'document', 'photo', 'video', 'voice'):
            if len(args) > 3:
                caption = args[3].replace('&cl', ':')
            else:
                caption = None
            if type == 'audio':
                self.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption,
                                    disable_notification=disable_notifications)
            elif type == 'document':
                self.bot.send_document(chat_id=chat_id, document=file_id, caption=caption,
                                       disable_notification=disable_notifications)
            elif type == 'photo':
                self.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption,
                                    disable_notification=disable_notifications)
            elif type == 'video':
                self.bot.send_video(chat_id=chat_id, video=file_id, caption=caption,
                                    disable_notification=disable_notifications)
            else:
                self.bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption,
                                    disable_notification=disable_notifications)
            return

    def push_post(self, bot, job):
        for i in range(0, self.config['per_post']):
            if len(self.queue) == 0:
                for admin_id in self.config['admins']:
                    self.bot.send_message(chat_id=admin_id, text="No more posts queued for %s!" % self.chat.title)
                return
            self.send_post(self.queue[0])
            del self.queue[0]
            if self.config['notify_low'] and len(self.queue) == self.config['notify_low_count']:
                for admin_id in self.config['admins']:
                    self.bot.send_message(chat_id=admin_id, text="There are fewer than %d posts queued for %s!" % (
                        self.config['notify_low_count'], self.chat.title))
        self.dump_data()

    def send_command(self, bot, update):
        if not self.is_admin(update):
            return
        if len(self.queue) > 0:
            self.send_post(self.queue[0])
            del self.queue[0]
        else:
            self.warning("queue empty", end=False)

    def queue_count(self, bot, update):
        bot.send_message(chat_id=update.message.chat_id,
                         text="There are currently *%d* posts queued for %s." % (len(self.queue), self.chat.title),
                         parse_mode='Markdown')

    def get_time(self, bot, update):
        time = datetime.datetime.now().time()
        bot.send_message(chat_id=update.message.chat_id, text=str(time))

    def warning(self, message, end=True):
        warning_message = self.bot_name + ": " + message
        logger.warning(warning_message)
        if end:
            self.should_end = True

    def is_admin(self, update):
        if update.message.from_user.id not in self.config['admins']:
            self.bot.send_message(chat_id=update.message.chat_id,
                                  text="You are not authorized to control the queue for %s" % self.chat.title)
            return False
        return True

    def dump_data(self, bot=None, update=None):
        self.config['queued_posts'] = self.queue
        global g_config
        g_config[self.token] = self.config
        dump_variables()


def main():
    get_config()
    active_bots = []
    for key in g_config:
        active_bots.append(ChannelQueue(key))
        active_bots[-1].start()


def start(bot, update):
    pass


def get_config():
    dir = os.path.dirname(__file__)
    path = os.path.join(dir, 'config.json')
    with open(path) as data_file:
        data = json.load(data_file)
    global g_config
    g_config = data


def dump_variables_loop(bot, job):
    dump_variables()


def dump_variables():
    data = json.dumps(g_config)
    dir = os.path.dirname(file_dir)
    path = os.path.join(dir, 'config.json')
    with open(path, "w") as f:
        f.write(data)


# logs bot errors thrown
def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"' % (update, error))


if __name__ == '__main__':
    main()

import datetime
from random import shuffle
from telegram import TelegramError, InlineKeyboardMarkup, InlineKeyboardButton

import channel_queue_bot


class ChannelInstanceHandler:
    def __init__(self, updater, channel_id, g_config):
        self.updater = updater
        self.bot = self.updater.bot
        self.channel_id = int(channel_id)
        self.g_config = g_config
        self.connect_channel()
        self.start_post_loops()
        self.logger = channel_queue_bot.logger

    def connect_channel(self):
        self.chat = self.bot.get_chat(chat_id=self.channel_id)
        if str(self.channel_id) not in self.g_config['channels']:
            self.config = {}
        else:
            self.config = self.g_config['channels'][str(self.channel_id)]
        self.assure_defaults()
        self.load_queue()
        self.update_admins()
        self.bot_name = "%s Queue Bot" % self.chat.title
        if self.chat.get_member(self.bot.get_me().id) is None:
            self.logger.warning("Not a member of %s" % self.chat.title)

    def assure_defaults(self):
        for key in self.g_config['default_settings']:
            if key not in self.config:
                if isinstance(self.g_config['default_settings'][key], list):
                    self.config[key] = []
                elif isinstance(self.g_config['default_settings'][key], dict):
                    self.config[key] = {}
                else:
                    self.config[key] = self.g_config['default_settings'][key]

    def update_admins(self):
        self.config['admins'] = []
        for admin in self.chat.get_administrators():
            if not admin.user.is_bot:
                self.config['admins'].append(admin.user.id)

    def load_queue(self):
        self.queue = []
        self.current_index = 0
        self.reference_index = -1
        max_index = -1
        read_queue = self.config['queued_posts']
        for message_data in read_queue:
            index = int(message_data.split(':')[0])
            if self.reference_index == -1:
                self.reference_index = index
            else:
                self.reference_index = min(self.reference_index, index)
                max_index = max(max_index, index)

        self.queue = ["null"] * (max_index - self.reference_index + 1)
        for message_data in read_queue:
            index = int(message_data.split(':')[0])
            self.queue[index - self.reference_index] = message_data
        self.queue[:] = [x for x in self.queue if x != "null"]

    def start_post_loops(self):
        times = self.config['post_times']
        for time_string in times:
            args = time_string.split(':')
            hours = int(args[0])
            minutes = int(args[1])
            time = datetime.time(hours, minutes)
            name = str(self.chat.id) + time_string
            self.updater.job_queue.run_daily(self.push_post, time, name=name)

    def shuffle(self, bot, update):
        # strip numbers from queue
        numberless = []
        for entry in self.queue:
            numberless.append(entry[entry.find(':'):])

        # shuffle new queue
        shuffle(numberless)

        # add renumber entries
        for index in range(0, len(numberless)):
            numberless[index] = str(index) + numberless[index]

        # set new queue as official queue
        self.queue = numberless

    def add_text(self, bot, update):
        if len(self.queue) == 0:
            next_index = 1
        else:
            next_index = int(self.queue[-1].split(":")[0]) + 1
        text = update.message.text_markdown
        text = text.replace(':', '&cl')
        data_text = "%d:t:%s" % (next_index, text)
        self.queue.append(data_text)
        self.post_queued_message(bot, update, next_index)

    def add_media(self, bot, update):
        message = update.message
        type = None
        if message.photo is not None:
            type = "p"
        elif message.video is not None:
            type = "v"
        elif message.audio is not None:
            type = "a"
        elif message.document is not None:
            type = "d"
        elif message.sticker is not None:
            type = "s"
        elif message.voice is not None:
            type = "vo"
        else:
            type = "vn"
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
        self.post_queued_message(bot, update, next_index)

    def post_queued_message(self, bot, update, post_id):
        post = update.message
        text = "Success! I've added that post to the queue for *%s*!" % self.chat.title
        keyboard = [[InlineKeyboardButton("Remove", callback_data="remove[&sp?]%s[&sp?]%d" % (self.chat.id, post_id))]]
        markup = InlineKeyboardMarkup(keyboard)
        post.reply_text(text=text, reply_markup=markup, parse_mode='Markdown', quote=True)

    def remove_post(self, bot, update, post_id):
        query = update.callback_query
        reply = "I couldn't find this post in the queue for *%s*." % self.chat.title
        for post_data in self.queue:
            if int(post_data.split(':')[0]) == post_id:
                self.queue.remove(post_data)
                reply = "This post has been removed from the queue for *%s*." % self.chat.title
        query.edit_message_text(text=reply, reply_markup=None, parse_mode='Markdown')

    def times(self, bot, update):
        if len(self.config['post_times']) == 0:
            bot.send_message(chat_id=update.message.chat_id,
                             text="There are no scheduled times for queued posts to be sent!\n\nUse /addtime to add post times for *%s*." % self.chat.title,
                             parse_mode='Markdown')
            return
        sorted_times = self.sort_times(update.message.from_user.id)
        text = "*Post times for %s in 24h format:*" % self.chat.title
        if not self.has_set_timezone(update.message.from_user.id):
            text = "You have not set a time zone with /timezone yet. Times will be displayed in UTC format.\n\n" + text
        for index in range(0, len(sorted_times)):
            if index < 9:
                indent = "   "
            else:
                indent = " "
            text += "\n%d.%s%s" % (index + 1, indent, sorted_times[index])
        bot.send_message(chat_id=update.message.chat_id, text=text, parse_mode='Markdown')

    def sort_times(self, user_id):
        # total number of times
        times_count = len(self.config['post_times'])
        # create a copy of times in user preferred time
        times = []
        for time in self.config['post_times']:
            times.append(self.to_pref_time(user_id, time))
        for index in range(0, times_count):
            (min_index, minimum) = (index, times[index].split(':'))
            for search_index in range(index, times_count):
                contender = times[search_index].split(':')
                if minimum == contender:
                    continue
                if int(minimum[0]) == int(contender[0]):
                    digit = 1
                else:
                    digit = 0
                winner_num = min(int(minimum[digit]), int(contender[digit]))
                if int(contender[digit]) == winner_num:
                    winner = contender
                    (min_index, minimum) = (search_index, times[search_index].split(':'))
                else:
                    continue
            temp = times[index]
            times[index] = times[min_index]
            times[min_index] = temp
        return times

    def add_time(self, bot, update, args):
        user_id = update.message.chat_id
        if len(args) == 0:
            bot.send_message(chat_id=user_id,
                             text="Follow the /addtime command with one or more of the desired times in 24h format.\n\nFor example:\n`/addtime 0:00 8:15 16:00`",
                             parse_mode='Markdown')
            return
        for time in args:
            if self.to_utc_time(user_id, time) in self.config['post_times']:
                bot.send_message(chat_id=user_id,
                                 text="*%s* has already been added as a post time for *%s*. Try again!" % (
                                 time, self.chat.title), parse_mode='Markdown')
                return
            if ':' not in time:
                bot.send_message(chat_id=user_id, text="Invalid time format for one or more arguments.")
                return
            try:
                units = time.split(':')
                hour = int(units[0])
                minute = int(units[1])
            except ValueError:
                bot.send_message(chat_id=user_id, text="Invalid time format for one or more arguments.")
                return
            if hour > 23 or hour < 0 or minute > 59 or minute < 0:
                bot.send_message(chat_id=user_id,
                                 text="Invalid time format for one or more arguments. Time out of range.")
                return
        for time_string in args:
            utc_time_string = self.to_utc_time(user_id, time_string)
            hour = int(utc_time_string.split(':')[0])
            minute = int(utc_time_string.split(':')[1])
            utc_time = datetime.time(hour, minute)
            self.config['post_times'].append(utc_time_string)
            name = str(self.chat.id) + utc_time_string
            self.updater.job_queue.run_daily(self.push_post, utc_time, name=name)
        if len(args) == 1:
            string = "that time"
        else:
            string = "those times"
        bot.send_message(chat_id=user_id, text="Ok, I'll start posting in *%s* at %s!" % (self.chat.title, string),
                         parse_mode='Markdown')

    def remove_time(self, bot, update, args):
        user_id = update.message.chat_id
        if len(args) == 0:
            bot.send_message(chat_id=user_id,
                             text="Follow the /removetime command with one or more of the times as they appear in /times.\n\nFor example:\n`/removetime 0:00 8:15 16:00`",
                             parse_mode='Markdown')
            return
        for time in args:
            if self.to_utc_time(user_id, time) not in self.config['post_times']:
                bot.send_message(chat_id=user_id,
                                 text="*%s* isn't a post time for *%s*. Try again!" % (time, self.chat.title),
                                 parse_mode='Markdown')
                return
            if ':' not in time:
                bot.send_message(chat_id=user_id, text="Invalid time format for one or more arguments.")
                return
            try:
                units = time.split(':')
                hour = int(units[0])
                minute = int(units[1])
            except ValueError:
                bot.send_message(chat_id=user_id, text="Invalid time format for one or more arguments.")
                return
            if hour > 23 or hour < 0 or minute > 59 or minute < 0:
                bot.send_message(chat_id=user_id,
                                 text="Invalid time format for one or more arguments. Time out of range.")
                return
        for time in args:
            utc_time_string = self.to_utc_time(user_id, time)
            hour = int(utc_time_string.split(':')[0])
            minute = int(utc_time_string.split(':')[1])
            utc_time = datetime.time(hour, minute)
            self.config['post_times'].remove(utc_time_string)
            name = str(self.chat.id) + utc_time_string
            for job in self.updater.job_queue.jobs():
                if job.name == name:
                    job.schedule_removal()
        if len(args) == 1:
            string = "that time"
        else:
            string = "those times"
        bot.send_message(chat_id=user_id,
                         text="Ok, I won't send posts to *%s* at %s anymore." % (self.chat.title, string),
                         parse_mode='Markdown')

    def send_post(self, data_text):
        disable_notifications = self.config['disable_notifications']
        args = data_text.split(':')
        type = args[1]
        chat_id = self.chat.id
        if type == 't':
            message_text = args[2].replace('&cl', ':')
            self.bot.send_message(chat_id=chat_id, text=message_text, parse_mode='Markdown',
                                  disable_notification=disable_notifications)
            return
        file_id = self.bot.get_file(args[2]).file_id
        if type in ('a', 'd', 'p', 'v', 'vo'):
            if len(args) > 3:
                caption = args[3].replace('&cl', ':')
            else:
                caption = None
            if type == 'a':
                self.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption,
                                    disable_notification=disable_notifications)
            elif type == 'd':
                self.bot.send_document(chat_id=chat_id, document=file_id, caption=caption,
                                       disable_notification=disable_notifications)
            elif type == 'p':
                self.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption,
                                    disable_notification=disable_notifications)
            elif type == 'v':
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

    def to_pref_time(self, user_id, time_string):
        if not self.has_set_timezone(user_id):
            return time_string
        utc_offset = int(self.g_config['timezone_prefs'][str(user_id)])
        args = time_string.split(':')
        hour = args[0]
        minute = int(args[1])
        hour = int(hour) + utc_offset
        if hour < 0:
            hour += 24
        elif hour > 23:
            hour -= 24
        time = datetime.time(hour, minute)
        string = time.strftime("%H:%M")
        if string[0] == '0':
            string = string[1:]
        return string

    def to_utc_time(self, user_id, time_string):
        if not self.has_set_timezone(user_id):
            return time_string
        utc_offset = int(self.g_config['timezone_prefs'][str(user_id)]) * -1
        args = time_string.split(':')
        hour = args[0]
        minute = int(args[1])
        hour = int(hour) + utc_offset
        if hour < 0:
            hour += 24
        elif hour > 23:
            hour -= 24
        time = datetime.time(hour, minute)
        string = time.strftime("%H:%M")
        if string[0] == '0':
            string = string[1:]
        return string

    def has_set_timezone(self, user_id):
        if str(user_id) in self.g_config['timezone_prefs']:
            return True
        return False

    def is_admin(self, update):
        if update.message.from_user.id not in self.config['admins']:
            self.bot.send_message(chat_id=update.message.chat_id,
                                  text="You are not authorized to control the queue for %s" % self.chat.title)
            return False
        return True

    def bot_is_admin(self, update):
        try:
            admins = self.chat.get_administrators()
            return True
        except TelegramError:
            return False

    def dump_data(self):
        self.config['queued_posts'] = self.queue
        self.g_config['channels'][str(self.channel_id)] = self.config

    def warning(self, text):
        channel_queue_bot.logger.warning("%s: %s" % (self.bot_name, text))

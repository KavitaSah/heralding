import base64
import smtpd
import asyncore
import asynchat
from smtpd import NEWLINE, EMPTYSTRING

from handlerbase import HandlerBase


class SMTPChannel(smtpd.SMTPChannel):
    def __init__(self, smtp_server, newsocket, fromaddr,
                 map=None, session=None, opts=None):
        self.options = opts

        # A sad hack because SMTPChannel doesn't 
        # allow custom banners, and sends it's own through its
        # __init__() method. When the initflag is False,
        # the push() method is effectively disabled, so the 
        # superclass banner is not sent.
        self._initflag = False
        self.banner = self.options['banner']
        smtpd.SMTPChannel.__init__(self, smtp_server, newsocket, fromaddr)
        asynchat.async_chat.__init__(self, newsocket, map=map)

        # Now we set the initflag, so that push() will work again.
        # And we push.
        self._initflag = True
        self.push("220 %s" % (self.banner))

        # States
        self.login_pass_authenticating = False
        self.login_uname_authenticating = False
        self.plain_authenticating = False
        self.authenticated = False

        self.username = None
        self.password = None

        self.session = session
        self.options = opts

    def push(self, msg):
        # Only send data after superclass initialization
        if self._initflag:
            asynchat.async_chat.push(self, msg + '\r\n')

    def close_quit(self):
        self.close_when_done()
        self.handle_close()

    def smtp_QUIT(self, arg):
        self.push('221 Bye')
        self.close_when_done()
        self.close_quit()

    def collect_incoming_data(self, data):
        self.__line.append(data)

    def smtp_EHLO(self, arg):
        if not arg:
            self.push('501 Syntax: HELO/EHLO hostname')
            return
        if self.__greeting:
            self.push('503 Duplicate HELO/EHLO')
        else:
            self.push('250-%s Hello %s' % (self.banner, arg))
            self.push('250-AUTH PLAIN LOGIN')
            self.push('250 EHLO')

    def smtp_AUTH(self, arg):

        if self.login_uname_authenticating:
            self.login_uname_authenticating = False
            self.username = base64.b64decode(arg)
            self.login_pass_authenticating = True
            return

        if self.login_pass_authenticating:
            self.login_pass_authenticating = False
            self.password = base64.b64decode(arg)
            self.session.try_login(self.username, self.password)
            self.push('535 authentication failed')
            self.close_quit()

        elif self.plain_authenticating:
            self.plain_authenticating = False
            # Our arg will ideally be the username/password
            self.username, _, self.password = base64.b64decode(arg).split('\x00')
            self.session.try_login(self.username, self.password)
            self.push('535 Authentication Failed')
            self.close_quit()

        elif 'PLAIN' in arg:
            self.plain_authenticating = True
            try:
                _, param = arg.split()
            except:
                # We need to get the credentials now since client has not sent
                # them. The space after the 334 is important as said in the RFC
                self.push("334 ")
                return
            self.username, _, self.password = base64.b64decode(param).split('\x00')
            self.session.try_login(self.username, self.password)
            #for now all authentications will fail
            self.push('535 Authentication Failed')
            self.close_quit()

        elif 'LOGIN' in arg:
            param = arg.split()
            if len(param) > 1:
                self.username = base64.b64decode(param[1])
                self.push('334 ' + base64.b64encode('Password:'))
                self.login_pass_authenticating = True
                return
            else:
                self.push('334 ' + base64.b64encode('Username:'))
                self.login_uname_authenticating = True
                return


    # This code is taken directly from the underlying smtpd.SMTPChannel
    # support for AUTH is added.
    def found_terminator(self):
        line = EMPTYSTRING.join(self.__line)

        if self.debug:
            self.logger.info('found_terminator(): data: %s' % repr(line))

        self.__line = []
        if self.__state == self.COMMAND:
            if not line:
                self.push('500 Error: bad syntax')
                return
            method = None
            i = line.find(' ')

            if (self.login_uname_authenticating or
                    self.login_pass_authenticating or
                    self.plain_authenticating):
                # If we are in an authenticating state, call the
                # method smtp_AUTH.
                arg = line.strip()
                command = 'AUTH'
            elif i < 0:
                command = line.upper()
                arg = None
            else:
                command = line[:i].upper()
                arg = line[i + 1:].strip()

            # White list of operations that are allowed prior to AUTH.
            if not command in ['AUTH', 'EHLO', 'HELO', 'NOOP', 'RSET', 'QUIT']:
                if not self.authenticated:
                    self.push('530 Authentication required')
                    self.close_quit()
                    return

            method = getattr(self, 'smtp_' + command, None)
            if not method:
                self.push('502 Error: command "%s" not implemented' % command)
                return
            method(arg)
            return
        else:
            if self.__state != self.DATA:
                self.push('451 Internal confusion')
                return
                # Remove extraneous carriage returns and de-transparency according
            # to RFC 821, Section 4.5.2.
            data = []
            for text in line.split('\r\n'):
                if text and text[0] == '.':
                    data.append(text[1:])
                else:
                    data.append(text)
            self.__data = NEWLINE.join(data)
            status = self.__server.process_message(
                self.__peer,
                self.__mailfrom,
                self.__rcpttos,
                self.__data
            )
            self.__rcpttos = []
            self.__mailfrom = None
            self.__state = self.COMMAND
            self.set_terminator('\r\n')
            if not status:
                self.push('250 Ok')
            else:
                self.push(status)


class DummySMTPServer(object):
    def process_message(self, peer, mailfrom, rcpttos, data):
        # Maybe this data should be logged, it might be interesting
        # print peer, mailfrom, rcpttos, data
        pass


class smtp(HandlerBase):
    def __init__(self, sessions, options):
        super(smtp, self).__init__(sessions, options)
        self._options = options

    def handle_session(self, gsocket, address):
        session_ = self.create_session(address, gsocket)
        local_map = {}
        SMTPChannel(DummySMTPServer(), gsocket, address, session=session_,
                    map=local_map, opts=self._options)
        asyncore.loop(map=local_map)
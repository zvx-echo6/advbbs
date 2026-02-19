"""
advBBS Command Dispatcher

Routes incoming messages to appropriate command handlers.
"""

import logging
import re
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.bbs import advBBS

logger = logging.getLogger(__name__)


class CommandDispatcher:
    """
    Dispatches commands to appropriate handlers.

    Commands are case-insensitive and can have arguments.
    """

    def __init__(self, bbs: "advBBS"):
        """
        Initialize dispatcher with BBS instance.

        Args:
            bbs: Parent BBS instance for accessing services
        """
        self.bbs = bbs

        # Command registry: command -> (handler_func, access_level, help_text)
        # Access levels: "always", "authenticated", "admin"
        self._commands = {}

        # Register built-in commands
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in commands."""
        # Help
        self.register("BBS", self.cmd_help, "always", "Show help")
        self.register("?", self.cmd_help, "always", "Show help")
        self.register("HELP", self.cmd_help, "always", "Show help")


        # Authentication
        self.register("REGISTER", self.cmd_register, "always", "Register: REGISTER <user> <pass>")
        self.register("LOGIN", self.cmd_login, "always", "Login: LOGIN <user> <pass>")
        self.register("LOGOUT", self.cmd_logout, "authenticated", "Log out")
        self.register("PASSWD", self.cmd_passwd, "authenticated", "Change password: PASSWD <old> <new>")

        # Node management
        self.register("ADDNODE", self.cmd_addnode, "authenticated", "Add node: ADDNODE <node_id>")
        self.register("AN", self.cmd_addnode, "authenticated", "Add node (short)")
        self.register("RMNODE", self.cmd_rmnode, "authenticated", "Remove node: RMNODE <node_id>")
        self.register("RMVNODE", self.cmd_rmnode, "authenticated", "Remove node (alias)")  # backward compat
        self.register("RN", self.cmd_rmnode, "authenticated", "Remove node (short)")
        self.register("NODES", self.cmd_nodes, "authenticated", "List your nodes")
        self.register("N", self.cmd_nodes, "authenticated", "List nodes (short)")

        # Mail commands
        self.register("SEND", self.cmd_send_mail, "authenticated", "Send mail: SEND <to[@bbs]> <msg>")
        self.register("S", self.cmd_send_mail, "authenticated", "Send mail (short)")
        self.register("MAIL", self.cmd_check_mail, "authenticated", "Check mail count")
        self.register("M", self.cmd_check_mail, "authenticated", "Check mail (short)")
        self.register("SENT", self.cmd_sent_mail, "authenticated", "Check sent mail status")
        self.register("READ", self.cmd_read, "sync_or_auth", "Read mail/post: READ [n]")
        self.register("DELETE", self.cmd_delete_mail, "authenticated", "Delete mail: DELETE <n>")
        self.register("DEL", self.cmd_delete_mail, "authenticated", "Delete mail (alias)")
        self.register("D", self.cmd_delete_mail, "authenticated", "Delete mail (short)")
        self.register("REPLY", self.cmd_reply, "authenticated", "Reply to mail: REPLY <n> <msg>")
        self.register("RE", self.cmd_reply, "authenticated", "Reply (short)")
        self.register("FORWARD", self.cmd_forward, "authenticated", "Forward mail: FORWARD <n> <to[@bbs]>")
        self.register("FWD", self.cmd_forward, "authenticated", "Forward (short)")

        # Board commands
        self.register("BOARD", self.cmd_boards, "always", "List/enter board: BOARD [name]")
        self.register("B", self.cmd_boards, "always", "Board (short)")
        self.register("LIST", self.cmd_list, "sync_or_auth", "List posts: LIST [start]")
        self.register("L", self.cmd_list, "sync_or_auth", "List posts (short)")
        self.register("POST", self.cmd_post, "authenticated", "Post: POST <subj> <body>")
        self.register("P", self.cmd_post, "authenticated", "Post (short)")
        self.register("QUIT", self.cmd_quit_board, "always", "Quit board")
        self.register("Q", self.cmd_quit_board, "always", "Quit (short)")

        # READ is registered above under mail commands, serves as short alias too
        self.register("R", self.cmd_read, "sync_or_auth", "Read (short)")

        # Peer/federation commands
        self.register("PEERS", self.cmd_peers, "always", "List connected BBS peers")

        # Info command
        self.register("INFO", self.cmd_info, "always", "BBS information")
        self.register("I", self.cmd_info, "always", "Info (short)")

        # Admin commands
        self.register("BAN", self.cmd_ban, "admin", "Ban user: BAN <user> [reason]")
        self.register("UNBAN", self.cmd_unban, "admin", "Unban user: UNBAN <user>")
        self.register("MKBOARD", self.cmd_mkboard, "admin", "Create board: MKBOARD <name> [desc]")
        self.register("MB", self.cmd_mkboard, "admin", "Create board (short)")
        self.register("RMBOARD", self.cmd_rmboard, "admin", "Delete board: RMBOARD <name>")
        self.register("RB", self.cmd_rmboard, "admin", "Delete board (short)")
        self.register("ANNOUNCE", self.cmd_announce, "admin", "Broadcast: ANNOUNCE <msg>")
        self.register("ANN", self.cmd_announce, "admin", "Announce (short)")

        # Self-destruct
        self.register("DESTRUCT", self.cmd_destruct, "authenticated", "Delete all your data")

    def register(
        self,
        command: str,
        handler,
        access: str,
        help_text: str
    ):
        """Register a command handler."""
        self._commands[command.upper()] = (handler, access, help_text)

    def dispatch(
        self,
        message: str,
        sender: str,
        channel: int,
        reply_id: int = None
    ) -> Optional[str]:
        """
        Dispatch a message to the appropriate command handler.

        Args:
            message: Raw message text
            sender: Sender node ID
            channel: Channel the message arrived on
            reply_id: Meshtastic reply message ID (if this is a reply)

        Returns:
            Response string or None if no response
        """
        if not message or not message.strip():
            return None

        message = message.strip()

        # Check for Meshtastic native reply (no ! prefix but has reply_id)
        if not message.startswith("!") and reply_id:
            return self._handle_native_reply(message, sender, reply_id, channel)

        # Require ! prefix for all commands
        if not message.startswith("!"):
            return None  # Ignore messages without ! prefix

        # Strip the ! prefix
        message = message[1:]
        if not message:
            return None

        # Parse command and arguments
        parts = message.split(maxsplit=1)
        cmd = parts[0].upper()
        args = parts[1] if len(parts) > 1 else ""

        # Look up command
        if cmd not in self._commands:
            if self.bbs.config.bbs.reply_to_unknown_commands:
                return "Unknown cmd. Send !bbs for help."
            return None  # Silently ignore unknown commands

        handler, access, _ = self._commands[cmd]

        # Check access
        session = self._get_session(sender)

        if access == "authenticated" and not session.get("user_id"):
            return "Login required. !login user pass"

        if access == "sync_or_auth":
            # Allow if authenticated OR if on a sync board
            if not session.get("user_id"):
                # Check if in a sync board
                if not self._is_on_sync_board(session):
                    return "Login required. !login user pass"

        if access == "admin" and not session.get("is_admin"):
            return "Admin access required."

        # Check feature availability
        if not self._check_feature(cmd):
            return "This feature is disabled on this BBS."

        # Execute command
        try:
            self.bbs.stats.commands_processed += 1
            return handler(sender, args, session, channel)
        except Exception as e:
            logger.error(f"Error executing {cmd}: {e}")
            self.bbs.stats.errors += 1
            return "Error processing command."

    def _handle_native_reply(self, message: str, sender: str, reply_id: int, channel: int) -> Optional[str]:
        """
        Handle a Meshtastic native reply (user used reply function).

        Looks up the reply context for the original message and handles accordingly.
        """
        # Get reply context from mesh interface
        context = self.bbs.mesh.get_reply_context(reply_id)
        if not context:
            # No context found - might be expired or unknown message
            logger.debug(f"No reply context for reply_id {reply_id}")
            return None

        context_type = context.get("type")
        context_data = context.get("data", {})

        logger.info(f"Handling native reply: type={context_type}, from={sender}")

        if context_type == "mail_read":
            # Reply to a mail message
            return self._handle_mail_reply(message, sender, context_data, channel)
        elif context_type == "board_view":
            # Post to the board they were viewing
            return self._handle_board_reply(message, sender, context_data, channel)
        else:
            logger.warning(f"Unknown reply context type: {context_type}")
            return None

    def _handle_mail_reply(self, message: str, sender: str, context_data: dict, channel: int) -> Optional[str]:
        """Handle a reply to a mail message."""
        session = self._get_session(sender)

        # Must be logged in
        if not session.get("user_id"):
            return "Login required"

        original_sender = context_data.get("from_username")
        original_sender_bbs = context_data.get("from_bbs")  # For remote mail
        original_subject = context_data.get("subject", "")

        if not original_sender:
            return "Could not determine who to reply to."

        # Format subject as "Re: <original>" if not already
        if original_subject and not original_subject.lower().startswith("re:"):
            subject = f"Re: {original_subject}"
        elif original_subject:
            subject = original_subject
        else:
            subject = "Re: (no subject)"

        # Truncate subject if too long
        if len(subject) > 30:
            subject = subject[:27] + "..."

        # Build recipient - include @BBS for remote mail
        if original_sender_bbs and original_sender_bbs != self.bbs.config.bbs.callsign:
            recipient = f"{original_sender}@{original_sender_bbs}"
        else:
            recipient = original_sender

        # Use cmd_send_mail which handles both local and remote delivery
        return self.cmd_send_mail(sender, f"{recipient} {message}", session, channel)

    def _handle_board_reply(self, message: str, sender: str, context_data: dict, channel: int) -> Optional[str]:
        """Handle a reply as a post to a board."""
        session = self._get_session(sender)

        board_name = context_data.get("board_name")
        board_id = context_data.get("board_id")

        if not board_id or not board_name:
            return "Could not determine which board to post to."

        # Check if login required for this board
        if not session.get("user_id"):
            # Check if it's a synced board (allows anonymous read/post)
            from ..core.boards import BoardRepository
            board_repo = BoardRepository(self.bbs.db)
            board_obj = board_repo.get_board_by_id(board_id)
            if not board_obj or not board_obj.sync_enabled:
                return f"Login required to post to {board_name}."

        # Post to the board
        try:
            user_id = session.get("user_id")
            username = session.get("username", sender[:8])

            result = self.bbs.board_service.post_message(
                board_id=board_id,
                user_id=user_id,
                body=message,
                username=username
            )
            if result:
                return f"Posted to {board_name}"
            else:
                return f"Failed to post to {board_name}."
        except Exception as e:
            logger.error(f"Error posting to board: {e}")
            return "Error posting."

    def _get_session(self, sender: str) -> dict:
        """Get or create session for sender, checking for timeout."""
        now = time.time()
        timeout_secs = self.bbs.config.bbs.session_timeout_minutes * 60

        if sender not in self.bbs._sessions:
            self.bbs._sessions[sender] = {
                "user_id": None,
                "username": None,
                "is_admin": False,
                "current_board": None,
                "last_activity": now,
            }
        else:
            session = self.bbs._sessions[sender]
            # Check for session timeout
            if session.get("user_id") and timeout_secs > 0:
                if now - session.get("last_activity", 0) > timeout_secs:
                    # Session expired - log out user
                    logger.info(f"Session timeout for {session.get('username')}")
                    session["user_id"] = None
                    session["username"] = None
                    session["is_admin"] = False
                    session["current_board"] = None

            # Update last activity
            session["last_activity"] = now

        return self.bbs._sessions[sender]

    def _check_feature(self, cmd: str) -> bool:
        """Check if the feature for this command is enabled."""
        mail_commands = {"SEND", "S", "MAIL", "M", "READ", "R", "DELETE", "DEL", "D", "REPLY", "RE", "FORWARD", "FWD", "SENT"}
        board_commands = {"BOARD", "B", "LIST", "L", "POST", "P", "QUIT", "Q"}

        if cmd in mail_commands and not self.bbs.config.features.mail_enabled:
            return False

        if cmd in board_commands and not self.bbs.config.features.boards_enabled:
            return False

        return True

    def _is_on_sync_board(self, session: dict) -> bool:
        """Check if user is currently in a synced board."""
        from ..core.boards import BoardRepository

        board_id = session.get("current_board")
        if not board_id:
            return False

        board_repo = BoardRepository(self.bbs.db)
        board = board_repo.get_board_by_id(board_id)
        if not board:
            return False

        return board.sync_enabled

    # === Command Handlers ===

    def cmd_help(self, sender: str, args: str, session: dict, channel: int):
        """Show help information - sends all help in multiple messages (<150 chars each)."""
        callsign = self.bbs.config.bbs.callsign

        # Check for admin help
        if args.strip().lower() == "admin":
            if not session.get("is_admin"):
                return "Admin access required."
            return (
                f"[{callsign}] Admin Commands\n"
                "!ban user [reason] - Ban user\n"
                "!unban user - Unban user\n"
                "!mkboard name [desc] - Create board\n"
                "!rmboard name - Delete board\n"
                "!announce msg - Broadcast"
            )

        # Build help messages - each under 175 chars
        # Show short aliases in parentheses
        help_msgs = [
            (
                f"[{callsign}] 1/3 Account\n"
                "!register user pass - New account\n"
                "!login user pass - Login\n"
                "!logout, !passwd old new\n"
                "!info (!i), !nodes (!n), !peers"
            ),
            (
                f"[{callsign}] 2/3 Mail\n"
                "!send (!s) user message\n"
                "!mail (!m) inbox, !sent outbox\n"
                "!read (!r) n, !reply (!re) n msg\n"
                "!delete (!d) n, !forward (!fwd) n user"
            ),
            (
                f"[{callsign}] 3/3 Boards\n"
                "!board (!b) - List or enter board\n"
                "!list (!l) posts, !read (!r) n\n"
                "!post (!p) subject message\n"
                "!quit (!q) - Exit board"
            ),
        ]

        # Return all messages as a list
        return help_msgs

    def cmd_info(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Show BBS information."""
        uptime_secs = int(self.bbs.uptime)
        uptime_str = f"{uptime_secs // 3600}h {(uptime_secs % 3600) // 60}m"

        return (
            f"=== {self.bbs.config.bbs.name} ===\n"
            f"Callsign: {self.bbs.config.bbs.callsign}\n"
            f"Mode: {self.bbs.config.operating_mode.mode}\n"
            f"Uptime: {uptime_str}\n"
            f"Users: {self.bbs.db.count_users()}\n"
            f"Messages: {self.bbs.db.count_messages()}"
        )

    def cmd_who(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Show online users."""
        active = []
        for node_id, sess in self.bbs._sessions.items():
            if sess.get("username"):
                active.append(sess["username"])

        if not active:
            return "No users currently logged in."

        return f"Online: {', '.join(active)}"

    def cmd_register(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Register new user."""
        reg_mode = self.bbs.config.features.registration_mode

        if reg_mode == "closed":
            return "Registration is closed."

        if reg_mode == "limited":
            whitelist = self.bbs.config.features.registration_whitelist
            if sender not in whitelist:
                return "Registration is by invitation only."

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: !register user pass"

        username, password = parts

        # Validate username
        if len(username) < 3 or len(username) > 16:
            return "Username must be 3-16 characters."

        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return "Username: letters, numbers, _ only"

        # Validate password
        if len(password) < 6:
            return "Password must be at least 6 characters."

        # Check if username exists
        from ..db.users import UserRepository
        user_repo = UserRepository(self.bbs.db)

        if user_repo.get_user_by_username(username):
            return "Username already taken."

        # Create user
        try:
            salt = self.bbs.crypto.generate_salt()
            password_hash = self.bbs.crypto.hash_password(password).encode()
            encryption_key = self.bbs.crypto.derive_key(password, salt)

            # Encrypt user key with master key for recovery
            recovery_key = self.bbs.master_key.encrypt_user_key(encryption_key)

            user = user_repo.create_user(
                username=username,
                password_hash=password_hash,
                salt=salt,
                encryption_key=self.bbs.master_key.encrypt_user_key(encryption_key),
                recovery_key_enc=recovery_key
            )

            # Auto-login
            session["user_id"] = user.id
            session["username"] = user.username
            session["is_admin"] = user.is_admin

            # Associate current node
            from ..db.users import NodeRepository, UserNodeRepository
            node_repo = NodeRepository(self.bbs.db)
            user_node_repo = UserNodeRepository(self.bbs.db)

            node = node_repo.get_or_create_node(sender)
            user_node_repo.associate_node(user.id, node.id, is_primary=True)

            self.bbs.stats.users_registered += 1

            return f"Welcome {username}! You are now registered and logged in."

        except Exception as e:
            logger.error(f"Registration error: {e}")
            return "Registration failed. Please try again."

    def cmd_login(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Login user."""
        if session.get("user_id"):
            return f"Already logged in as {session['username']}. !logout first."

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: !login user pass"

        username, password = parts

        from ..db.users import UserRepository
        user_repo = UserRepository(self.bbs.db)

        user = user_repo.get_user_by_username(username)
        if not user:
            return "Invalid username or password."

        if user.is_banned:
            return f"Account banned: {user.ban_reason or 'No reason given'}"

        # Verify password
        try:
            if not self.bbs.crypto.verify_password(password, user.password_hash.decode()):
                return "Invalid username or password."
        except Exception:
            return "Invalid username or password."

        # Verify node association (2FA - must login from registered node)
        from ..db.users import NodeRepository, UserNodeRepository
        node_repo = NodeRepository(self.bbs.db)
        user_node_repo = UserNodeRepository(self.bbs.db)

        # Get node DB record for sender
        node = node_repo.get_node_by_id(sender)
        if node:
            # Check if this node is associated with the user
            if not user_node_repo.is_node_associated(user.id, node.id):
                return "Node not authorized for this account"
        else:
            # Node not in database at all - definitely not associated
            return "Node not authorized for this account"

        # Update session
        session["user_id"] = user.id
        session["username"] = user.username
        session["is_admin"] = user.is_admin

        # Update last seen
        user_repo.update_last_seen(user.id)

        # Check for mail
        from ..db.messages import MessageRepository
        msg_repo = MessageRepository(self.bbs.db)
        unread = msg_repo.count_unread_mail(user.id)

        mail_notice = f" You have {unread} unread message(s)." if unread else ""

        return f"Welcome back, {username}!{mail_notice}"

    def cmd_logout(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Logout user."""
        username = session.get("username", "user")
        session["user_id"] = None
        session["username"] = None
        session["is_admin"] = False
        session["current_board"] = None

        return f"Goodbye, {username}!"

    def cmd_passwd(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Change password."""
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: !passwd old new"

        old_pass, new_pass = parts

        if len(new_pass) < 6:
            return "New password must be at least 6 characters."

        from ..db.users import UserRepository
        user_repo = UserRepository(self.bbs.db)

        user = user_repo.get_user_by_id(session["user_id"])
        if not user:
            return "Error: User not found."

        # Verify old password
        if not self.bbs.crypto.verify_password(old_pass, user.password_hash.decode()):
            return "Current password is incorrect."

        # Generate new credentials
        salt = self.bbs.crypto.generate_salt()
        password_hash = self.bbs.crypto.hash_password(new_pass).encode()
        encryption_key = self.bbs.crypto.derive_key(new_pass, salt)

        user_repo.update_password(
            user.id,
            password_hash,
            salt,
            self.bbs.master_key.encrypt_user_key(encryption_key)
        )

        return "Password changed successfully."

    def cmd_addnode(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Add a node to user's account: ADDNODE <node_id>"""
        if not args:
            return "Usage: !addnode !nodeid"

        node_id = args.strip()

        # Validate node ID format (should start with !)
        if not node_id.startswith("!"):
            return "Invalid node ID (must start with !)"

        from ..db.users import NodeRepository, UserNodeRepository

        node_repo = NodeRepository(self.bbs.db)
        user_node_repo = UserNodeRepository(self.bbs.db)

        # Check if already associated
        existing_nodes = user_node_repo.get_user_nodes(session["user_id"])
        if node_id in existing_nodes:
            return f"Node {node_id} is already associated with your account."

        node = node_repo.get_or_create_node(node_id)
        user_node_repo.associate_node(session["user_id"], node.id)

        return f"Node {node_id} added. You can now login from that device."

    def cmd_rmnode(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Remove a node from user's account: RMNODE <node_id>"""
        if not args:
            return "Usage: !rmnode <node_id>"

        node_to_remove = args.strip()

        from ..db.users import UserNodeRepository
        user_node_repo = UserNodeRepository(self.bbs.db)

        # Get current nodes
        nodes = user_node_repo.get_user_nodes(session["user_id"])

        # Don't allow removing last node
        if len(nodes) <= 1:
            return "Cannot remove your only node. Add another first."

        # Don't allow removing current node (would lock self out)
        if node_to_remove == sender:
            return "Cannot remove the node you're currently using."

        if user_node_repo.remove_node(session["user_id"], node_to_remove):
            return f"Node {node_to_remove} removed."
        return "Node not found or not associated with your account."

    def cmd_nodes(self, sender: str, args: str, session: dict, channel: int) -> str:
        """List user's associated nodes."""
        from ..db.users import UserNodeRepository
        user_node_repo = UserNodeRepository(self.bbs.db)

        nodes = user_node_repo.get_user_nodes(session["user_id"])
        if not nodes:
            return "No nodes associated with your account."

        # Mark current node
        node_list = []
        for n in nodes:
            if n == sender:
                node_list.append(f"{n} (current)")
            else:
                node_list.append(n)

        return "Your nodes:\n" + "\n".join(node_list)

    # === Mail Commands ===

    def cmd_send_mail(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Send mail: SEND <to[@bbs]> [s:subject] <message>"""
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: !send user [s:subject] message"

        recipient, rest = parts

        # Parse optional s:subject prefix
        subject = None
        body = rest
        if rest.startswith("s:"):
            # Find where subject ends (next space after s:)
            subj_match = rest[2:].split(maxsplit=1)
            if len(subj_match) >= 1:
                subject = subj_match[0]
                body = subj_match[1] if len(subj_match) > 1 else ""

        if not body:
            return "Usage: !send user [s:subject] message"

        if len(body) > 1000:
            return "Message too long (max 1000 chars)."

        # If no explicit subject, use first word as subject
        if not subject:
            first_word = body.split()[0] if body.split() else ""
            subject = first_word[:20] if first_word else None

        # Check for remote addressing (user@bbs)
        if "@" in recipient:
            username, remote_bbs = recipient.split("@", 1)
            # Note: remote mail doesn't support subject yet - subject is embedded in body
            return self._send_remote_mail(session, sender, username, remote_bbs, body)

        # Local mail
        message, error = self.bbs.mail_service.compose_mail(
            sender_user_id=session["user_id"],
            sender_node_id=sender,
            recipient_username=recipient,
            body=body,
            subject=subject
        )

        if error:
            return error

        return f"Sent to {recipient}."

    def _send_remote_mail(self, session: dict, sender_node: str, recipient: str, remote_bbs: str, body: str) -> str:
        """Send mail to a user on a remote BBS."""
        # Find the peer BBS
        if not self.bbs.sync_manager:
            return "Remote mail not available (sync disabled)."

        # Pre-flight check: max 450 chars for remote mail
        if len(body) > 450:
            return f"Message too long for remote delivery (max 450 chars, yours: {len(body)})"

        # Check if we know the destination or can relay
        peer = self.bbs.sync_manager.get_peer_by_name(remote_bbs)
        if not peer and not self.bbs.sync_manager.list_peers():
            return f"No route to {remote_bbs}. No peers configured."

        # Get sender username and node
        from ..db.users import UserRepository, NodeRepository
        user_repo = UserRepository(self.bbs.db)
        node_repo = NodeRepository(self.bbs.db)

        sender_user = user_repo.get_user_by_id(session["user_id"])
        if not sender_user:
            return "Error: sender not found."

        sender_node_record = node_repo.get_node_by_id(sender_node)
        if not sender_node_record:
            return "Error: sender node not found."

        # Queue remote mail for sync
        from ..db.messages import MessageRepository
        msg_repo = MessageRepository(self.bbs.db)

        message = msg_repo.create_remote_mail(
            sender_username=sender_user.username,
            sender_bbs=self.bbs.config.bbs.callsign,
            sender_node_id=sender_node_record.id,
            recipient_username=recipient,
            recipient_bbs=remote_bbs.upper(),
            body=body,
            origin_bbs=self.bbs.config.bbs.callsign
        )

        if message:
            # Trigger immediate send via sync manager
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self.bbs.sync_manager.send_remote_mail(
                        sender_username=sender_user.username,
                        sender_bbs=self.bbs.config.bbs.callsign,
                        recipient_username=recipient,
                        recipient_bbs=remote_bbs.upper(),
                        body=body,
                        mail_uuid=message.uuid
                    )
                )
            except RuntimeError:
                # No running loop - mail will be sent by scheduled sync
                pass
            return f"Mail queued for {recipient}@{remote_bbs}."
        return "Failed to queue remote mail."

    def cmd_check_mail(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Check mail count and list. Usage: !mail [start] - show 5 messages starting at #start."""
        summary = self.bbs.mail_service.get_inbox_summary(session["user_id"])
        total = summary["total"]

        if total == 0:
            return "Inbox empty"

        # Parse optional start index
        limit = 5
        start_idx = None
        if args:
            try:
                start_idx = int(args)
                if start_idx < 1:
                    start_idx = 1
            except ValueError:
                return "Usage: !mail [start_number]"

        # Default: show the LAST 5 messages (highest numbered = newest)
        # #1 = oldest, #total = newest
        if start_idx is None:
            start_idx = max(1, total - limit + 1)

        if start_idx > total:
            return f"No messages at #{start_idx}. You have {total} messages."

        # Messages are stored newest first (DESC), but we want #1 = oldest
        # So offset from the END: to get #1, offset = total - 1
        # To get #start_idx, offset = total - start_idx - (limit - 1), then reverse
        end_idx = min(start_idx + limit - 1, total)
        count = end_idx - start_idx + 1

        # Offset for DESC query: total - end_idx
        offset = total - end_idx
        messages = self.bbs.mail_service.list_mail(session["user_id"], limit=count, offset=offset)
        # Reverse so oldest of this batch is first
        messages = list(reversed(messages))

        # Compact header: "2 new/5 tot (6-10)"
        lines = [f"{summary['unread']} new/{total} tot ({start_idx}-{end_idx})"]

        if messages:
            # Build index map
            mail_index_map = {}
            for i, msg in enumerate(messages):
                idx = start_idx + i
                mail_index_map[idx] = msg["id"]
                # Compact: "*1 12/16 14:30 bob subj" (marker, index, date+time, from, subject)
                marker = "*" if msg["new"] else " "
                # Truncate from to 8 chars, subject to remaining space
                from_name = msg['from'][:8]
                subj = msg.get('subject') or ""
                if subj:
                    subj = subj[:12]  # Truncate subject to keep line short
                    lines.append(f"{marker}{idx} {msg['date']} {from_name} {subj}")
                else:
                    lines.append(f"{marker}{idx} {msg['date']} {from_name}")
            session["mail_index_map"] = mail_index_map

        return "\n".join(lines)

    def cmd_reply(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Reply to mail: REPLY [n] <message> or REPLY <message> for last read"""
        if not args:
            return "Usage: !reply [msg_id] <message>"

        # Check if first arg is a message ID
        parts = args.split(maxsplit=1)
        message_id = None
        reply_body = args

        if len(parts) >= 2:
            try:
                message_id = int(parts[0])
                reply_body = parts[1]
            except ValueError:
                # First part isn't a number, use last read message
                pass

        # Get original message
        if message_id:
            mail, error = self.bbs.mail_service.read_mail(session["user_id"], message_id)
            if error:
                return error
            reply_to = mail["from"]
            reply_to_bbs = mail.get("from_bbs")
        elif session.get("last_mail_from"):
            reply_to = session["last_mail_from"]
            reply_to_bbs = session.get("last_mail_from_bbs")
        else:
            return "No message. Use !read n first"

        # Build recipient address
        if reply_to_bbs and reply_to_bbs != self.bbs.config.bbs.callsign:
            recipient = f"{reply_to}@{reply_to_bbs}"
        else:
            recipient = reply_to

        # Send reply
        return self.cmd_send_mail(sender, f"{recipient} {reply_body}", session, channel)

    def cmd_forward(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Forward mail: FORWARD <n> <to[@bbs]> or FORWARD <to[@bbs]> for last read"""
        if not args:
            return "Usage: !forward [msg_id] <user[@bbs]>"

        parts = args.split(maxsplit=1)
        message_id = None
        forward_to = args

        # Check if first arg is a message ID
        if len(parts) >= 2:
            try:
                message_id = int(parts[0])
                forward_to = parts[1]
            except ValueError:
                pass

        # Get original message
        if message_id:
            mail, error = self.bbs.mail_service.read_mail(session["user_id"], message_id)
            if error:
                return error
        elif session.get("last_mail_id"):
            mail, error = self.bbs.mail_service.read_mail(session["user_id"], session["last_mail_id"])
            if error:
                return error
        else:
            return "No message. Use !read n first"

        # Build forwarded message body
        fwd_body = f"[FWD from {mail['from']}]\n{mail['body']}"

        # Send forwarded message
        return self.cmd_send_mail(sender, f"{forward_to} {fwd_body}", session, channel)

    def cmd_delete_mail(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Delete mail: DELETE <n>"""
        if not args:
            return "Usage: !delete <message_id>"

        try:
            message_id = int(args)
        except ValueError:
            return "Usage: !delete <message_id>"

        success, error = self.bbs.mail_service.delete_mail(session["user_id"], message_id)
        if error:
            return error

        return f"Message #{message_id} deleted."

    def cmd_sent_mail(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Check sent remote mail status."""
        from ..db.messages import MessageRepository
        msg_repo = MessageRepository(self.bbs.db)

        sent = msg_repo.get_sent_remote_mail(session["user_id"], limit=5)

        if not sent:
            return "No sent remote mail."

        # Status icons
        status_icons = {
            "delivered": "+",
            "pending": "?",
            "failed": "!"
        }

        lines = ["Sent remote mail:"]
        for mail in sent:
            icon = status_icons.get(mail["status"], "?")
            # Format: "+12/16 user@BBS" or "?12/16 user@BBS"
            lines.append(f"{icon}{mail['date']} {mail['to']}")

        lines.append("")
        lines.append("+ delivered, ? pending, ! failed")

        return "\n".join(lines)

    # === Peer/Federation Commands ===

    def cmd_peers(self, sender: str, args: str, session: dict, channel: int) -> str:
        """List connected BBS peers for remote mail."""
        if not self.bbs.sync_manager:
            return "Sync/federation not enabled."

        peers = self.bbs.sync_manager.list_peers()
        if not peers:
            return "No peers configured"

        lines = ["=== BBS Peers ===", ""]
        for peer in peers:
            status = "online" if peer.get("online") else "offline"
            lines.append(f"  {peer['name']:<12} [{status}]")

        lines.append("")
        lines.append("Send mail: !send user@PEERNAME message")

        return "\n".join(lines)

    # === Board Commands ===

    def cmd_boards(self, sender: str, args: str, session: dict, channel: int) -> str:
        """List boards or enter: B [name]"""
        if not args:
            # List boards
            boards = self.bbs.board_service.list_boards(session.get("user_id"))

            if not boards:
                return "No boards available."

            lines = ["=== Boards ===", ""]
            for board in boards:
                unread_marker = f"({board['unread']} new)" if board['unread'] > 0 else ""
                restricted = "[R]" if board['restricted'] else ""
                lines.append(f"  {board['name']:<12} {board['posts']:3d} posts {unread_marker} {restricted}")

            lines.append("")
            lines.append("Use !board <name> to enter a board")

            return "\n".join(lines)

        # Enter board
        board, error = self.bbs.board_service.enter_board(args, session.get("user_id"))
        if error:
            return error

        session["current_board"] = board.id

        # Get post count
        from ..db.messages import MessageRepository
        msg_repo = MessageRepository(self.bbs.db)
        post_count = msg_repo.count_board_messages(board.id)

        lines = [
            f"=== {board.name.upper()} ===",
            board.description or "",
            f"{post_count} posts",
            "",
            "!L - list, !R <n> - read, !P <subj> <body> - post, !Q - quit",
            "Or just reply to post a message"
        ]
        response = "\n".join(lines)

        # Return with reply context for Meshtastic native reply support
        reply_context = {
            "type": "board_view",
            "data": {
                "board_id": board.id,
                "board_name": board.name
            },
            "ttl": 600  # 10 minutes to reply/post
        }

        return (response, reply_context)

    def cmd_list(self, sender: str, args: str, session: dict, channel: int) -> str:
        """List posts: L [start] - show 5 posts starting at #start (default: last 5)"""
        if not session.get("current_board"):
            return "Enter a board first with !board <name>"

        # Get total post count first
        total = self.bbs.board_service.count_posts(session["current_board"])
        if total == 0:
            return "No posts on this board."

        limit = 5
        start_num = None

        if args:
            try:
                start_num = int(args)
                if start_num < 1:
                    start_num = 1
            except ValueError:
                return "Usage: !L [start_number]"

        # Calculate offset - posts are numbered 1 to total (oldest=1)
        if start_num:
            # User wants posts starting at #start_num
            offset = start_num - 1
            if offset >= total:
                return f"No posts at #{start_num}. Board has {total} posts."
        else:
            # Default: show last 5 posts
            offset = max(0, total - limit)
            start_num = offset + 1

        posts = self.bbs.board_service.list_posts(
            session["current_board"],
            session.get("user_id"),
            limit=limit,
            offset=offset
        )

        if not posts:
            return "No posts on this board."

        # Show range info
        end_num = min(start_num + len(posts) - 1, total)
        lines = [f"Posts {start_num}-{end_num} of {total}:", "#    Date   Author       Subject", "-" * 40]
        for post in posts:
            lines.append(f"{post.number:3d}. {post.date} {post.author[:12]:<12} {post.subject}")

        response = "\n".join(lines)

        # Get board info for reply context
        from ..core.boards import BoardRepository
        board_repo = BoardRepository(self.bbs.db)
        board = board_repo.get_board_by_id(session["current_board"])

        if board:
            reply_context = {
                "type": "board_view",
                "data": {
                    "board_id": board.id,
                    "board_name": board.name
                },
                "ttl": 600  # 10 minutes to reply/post
            }
            return (response, reply_context)

        return response

    def cmd_read(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Read mail or post based on context: READ [n]"""
        # If in a board, read post; otherwise read mail
        if session.get("current_board"):
            return self._read_post(sender, args, session, channel)
        else:
            return self._read_mail(sender, args, session, channel)

    def _read_post(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Read a post from the current board."""
        if not args:
            return "Usage: !read <post_number>"

        try:
            post_number = int(args)
        except ValueError:
            return "Usage: !read <post_number>"

        post, error = self.bbs.board_service.read_post(
            session["current_board"],
            post_number,
            session.get("user_id")
        )

        if error:
            return error

        lines = [
            f"=== {post.board}#{post.number} ===",
            f"Subject: {post.subject}",
            f"From: {post.author}",
            f"Date: {post.date}",
            "",
            post.body
        ]

        return "\n".join(lines)

    def _read_mail(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Read mail (requires authentication)."""
        # Mail always requires authentication
        if not session.get("user_id"):
            return "Login required. !login user pass"

        if not args:
            # List mail - compact format
            messages = self.bbs.mail_service.list_mail(session["user_id"], limit=10)

            if not messages:
                return "Inbox empty"

            # Build index-to-ID mapping for user-friendly numbering
            mail_index_map = {}
            lines = []
            for idx, msg in enumerate(messages, 1):
                mail_index_map[idx] = msg["id"]
                # Compact: "*1 12/16 14:30 bob" (marker, index, date+time, from)
                marker = "*" if msg["new"] else " "
                lines.append(f"{marker}{idx} {msg['date']} {msg['from'][:8]}")

            # Store mapping in session for !read command
            session["mail_index_map"] = mail_index_map

            return "\n".join(lines)

        # Handle "!read new" or "!read" - read first unread message
        if not args or args.lower() == "new":
            messages = self.bbs.mail_service.list_mail(session["user_id"], unread_only=True, limit=1)
            if not messages:
                return "No unread messages."
            message_id = messages[0]["id"]
            index = 1  # Display as #1
        else:
            try:
                index = int(args)
            except ValueError:
                return "Usage: !read [new|number]"

            # Look up actual message ID from index
            mail_index_map = session.get("mail_index_map")

            # Build index map if not cached
            # Index order: #1 = oldest, #N = newest (same as !mail display)
            if not mail_index_map:
                messages = self.bbs.mail_service.list_mail(session["user_id"], limit=100)
                # messages are in DESC order (newest first), so reverse for index mapping
                messages = list(reversed(messages))
                mail_index_map = {idx: msg["id"] for idx, msg in enumerate(messages, 1)}
                session["mail_index_map"] = mail_index_map

            message_id = mail_index_map.get(index)

            if not message_id:
                return f"No message #{index} in inbox."

        mail, error = self.bbs.mail_service.read_mail(session["user_id"], message_id)
        if error:
            return error

        # Store last read message for REPLY
        session["last_mail_id"] = mail["id"]
        session["last_mail_from"] = mail["from"]
        session["last_mail_from_bbs"] = mail.get("from_bbs")  # For remote mail

        # Compact header: "#1 bob 12/16: Subject" or "#1 bob@MV51 12/16" for remote
        short_date = mail['date'][5:10] if len(mail['date']) >= 10 else mail['date']
        subject = mail['subject'] if mail['subject'] != "(no subject)" else ""

        # Show full user@bbs for remote mail
        from_display = mail['from']
        if mail.get('from_bbs'):
            from_display = f"{mail['from']}@{mail['from_bbs']}"

        if subject:
            header = f"#{index} {from_display} {short_date}: {subject}"
        else:
            header = f"#{index} {from_display} {short_date}"

        # Body with minimal footer
        lines = [header, "", mail['body']]
        response = "\n".join(lines)

        # Return with reply context for Meshtastic native reply support
        reply_context = {
            "type": "mail_read",
            "data": {
                "from_username": mail["from"],
                "from_bbs": mail.get("from_bbs"),  # For remote mail replies
                "subject": mail.get("subject", ""),
                "mail_id": mail["id"]
            },
            "ttl": 300  # 5 minutes to reply
        }

        return (response, reply_context)

    def cmd_post(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Post to board: P <subject> <body>"""
        if not session.get("current_board"):
            return "Enter a board first with !board <name>"

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: !P <subject> <body>"

        subject, body = parts

        message, error = self.bbs.board_service.create_post(
            board_id=session["current_board"],
            user_id=session["user_id"],
            sender_node_id=sender,
            subject=subject,
            body=body
        )

        if error:
            return error

        return "Post created successfully."

    def cmd_quit_board(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Quit current board."""
        if not session.get("current_board"):
            return "Not currently in a board."

        session["current_board"] = None
        return "Exited board."

    def cmd_ban(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Ban user (admin only)."""
        parts = args.split(maxsplit=1)
        if not parts:
            return "Usage: !ban <username> [reason]"

        username = parts[0]
        reason = parts[1] if len(parts) > 1 else "No reason given"

        from ..db.users import UserRepository
        user_repo = UserRepository(self.bbs.db)

        if user_repo.ban_user(username, reason, session["username"]):
            return f"User {username} has been banned."
        return "User not found."

    def cmd_unban(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Unban user (admin only)."""
        if not args:
            return "Usage: !unban <username>"

        from ..db.users import UserRepository
        user_repo = UserRepository(self.bbs.db)

        if user_repo.unban_user(args):
            return f"User {args} has been unbanned."
        return "User not found."

    def cmd_mkboard(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Create a new board (admin only)."""
        parts = args.split(maxsplit=1)
        if not parts:
            return "Usage: !mkboard <name> [description]"

        name = parts[0].lower()
        description = parts[1] if len(parts) > 1 else ""

        # Validate name
        if len(name) < 2 or len(name) > 16:
            return "Board name must be 2-16 characters."

        if not re.match(r'^[a-z0-9_]+$', name):
            return "Board name: lowercase letters, numbers, underscore only."

        board, error = self.bbs.board_service.create_board(
            name=name,
            description=description,
            creator_id=session["user_id"]
        )

        if error:
            return error

        return f"Board '{name}' created."

    def cmd_rmboard(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Delete a board (admin only)."""
        if not args:
            return "Usage: !rmboard <name>"

        name = args.strip().lower()

        success, error = self.bbs.board_service.delete_board(name)
        if error:
            return error

        return f"Board '{name}' deleted."

    def cmd_announce(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Broadcast announcement (admin only)."""
        if not args:
            return "Usage: !announce <message>"
        # TODO: Implement
        return "Announcement system not yet implemented."

    def cmd_destruct(self, sender: str, args: str, session: dict, channel: int) -> str:
        """Delete all user data."""
        if args != "CONFIRM":
            return "WARNING: Deletes ALL data. Type !destruct CONFIRM"

        from ..db.users import UserRepository
        from ..db.messages import MessageRepository

        user_repo = UserRepository(self.bbs.db)
        msg_repo = MessageRepository(self.bbs.db)

        user_id = session["user_id"]

        # Delete all messages
        deleted = msg_repo.delete_user_messages(user_id)

        # Delete user
        user_repo.delete_user(user_id)

        # Clear session
        session["user_id"] = None
        session["username"] = None
        session["is_admin"] = False

        return f"All your data has been deleted ({deleted} messages removed)."

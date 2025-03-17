try:
    print("Script starting...")

    from gevent import monkey
    print("Importing gevent...")
    # monkey.patch_all()
    print("Monkey patching done.")

    from flask import Flask
    from flask_socketio import SocketIO
    print("Flask and SocketIO imported.")

    app = Flask(__name__)
    print("Flask app created.")
    socketio = SocketIO(app)
    print("SocketIO initialized.")

    @socketio.on('connect')
    def handle_connect():
        print("Client connected")
        return True

    if __name__ == '__main__':
        print("Starting server on port 8081...")
        socketio.run(app, host='0.0.0.0', port=8081)

except Exception as e:
    print(f"Error occurred: {e}")
    import traceback
    traceback.print_exc()
import os

from flask import Flask, Response

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))


def read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


@app.errorhandler(Exception)
def show_error(e):
    return f"{type(e).__name__}: {e}\n\nBASE = {BASE}\nFiles here: {sorted(os.listdir(BASE))}", 500


@app.route("/")
def index():
    return Response(read("report.html"), mimetype="text/html")


@app.route("/data.js")
def data_js():
    return Response(read("data.js"), mimetype="application/javascript")


if __name__ == "__main__":
    app.run(debug=True)
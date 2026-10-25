import os

from flask import Flask, Response

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))


def read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


@app.route("/")
def index():
    return Response(read("report.html"), mimetype="text/html")


@app.route("/data.js")
def data_js():
    return Response(read("data.js"), mimetype="application/javascript")


if __name__ == "__main__":
    app.run(debug=True)
"""A WSGI app that does nothing, for isolating where throughput is being lost.

Run it under the *same* gunicorn config as the real app:

    gunicorn -c deploy/gunicorn.conf.py --bind 127.0.0.1:8002 scripts.hello_wsgi:application

then benchmark it the same way:

    ab -k -n 20000 -c 200 http://127.0.0.1:8002/

This imports nothing - no Django, no settings, no database. Whatever it scores
is the ceiling imposed by the hardware, the kernel, gunicorn and the load
generator, before any application code exists. Comparing it against /healthz/
splits the problem cleanly in two:

  * hello is also slow  -> the loss is in the box, gunicorn, or the benchmark
                           client. Nothing in the Django app can explain it.
  * hello is fast       -> the loss is in the application or its settings, and
                           /healthz/ is the place to look.
"""


def application(environ, start_response):
    body = b'ok'
    start_response('200 OK', [
        ('Content-Type', 'text/plain'),
        ('Content-Length', str(len(body))),
    ])
    return [body]

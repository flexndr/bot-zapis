"""Telegram-бот записи клиентов в салон: услуга → мастер → день → время → телефон.

Запуск:  BOT_TOKEN=... ADMIN_ID=... python bot.py
Хранение — SQLite (zapis.db рядом с файлом). Напоминание клиенту за 2 часа до визита,
уведомление администратору о каждой записи, отмена записи клиентом, /zapisi — список на сегодня и завтра для админа.
"""
import asyncio, os, sqlite3, datetime as dt
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove)

TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
DB = Path(__file__).with_name('zapis.db')

# Справочник салона: правится здесь или переносится в Google Таблицу
USLUGI = {'strizhka': ('Стрижка', 60, 1500), 'okrash': ('Окрашивание', 120, 4500),
          'manik': ('Маникюр', 90, 2000), 'brovi': ('Брови', 30, 900)}
MASTERA = {'anna': ('Анна', ['strizhka', 'okrash']), 'olga': ('Ольга', ['manik', 'brovi']),
           'irina': ('Ирина', ['strizhka', 'brovi'])}
RABOCHIE_CHASY = range(10, 20)   # 10:00–19:00, слот — 1 час
DNEY_VPERED = 7
DNI = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']

bot = Bot(TOKEN)
dp = Dispatcher()
chernoviki = {}   # user_id -> выбор клиента до подтверждения


def db():
    c = sqlite3.connect(DB)
    c.execute('''create table if not exists zapisi (id integer primary key, user_id int, imya text, telefon text,
                 usluga text, master text, kogda text, napomnil int default 0, otmenena int default 0)''')
    return c


def zanyato(master, den):
    with db() as c:
        rows = c.execute('select kogda from zapisi where master=? and kogda like ? and otmenena=0',
                         (master, den + '%')).fetchall()
    return {r[0][11:16] for r in rows}


def kb(ryady):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in ryady])


@dp.message(CommandStart())
async def start(m: Message):
    chernoviki.pop(m.from_user.id, None)
    await m.answer('Здравствуйте! Я запишу вас в салон за минуту. Выберите услугу:',
                   reply_markup=kb([[(f'{n} · {c} ₽', f'u:{k}')] for k, (n, _, c) in USLUGI.items()] +
                                   [[('Мои записи', 'moi')]]))


@dp.callback_query(F.data.startswith('u:'))
async def vybor_uslugi(q: CallbackQuery):
    u = q.data[2:]
    chernoviki[q.from_user.id] = {'usluga': u}
    mastera = [(n, f'm:{k}') for k, (n, us) in MASTERA.items() if u in us]
    await q.message.edit_text(f'{USLUGI[u][0]}. К какому мастеру?', reply_markup=kb([mastera]))


@dp.callback_query(F.data.startswith('m:'))
async def vybor_mastera(q: CallbackQuery):
    chernoviki[q.from_user.id]['master'] = q.data[2:]
    segodnya = dt.date.today()
    dni = [segodnya + dt.timedelta(days=i) for i in range(DNEY_VPERED)]
    knopki = [(f'{DNI[d.weekday()]} {d:%d.%m}', f'd:{d.isoformat()}') for d in dni]
    await q.message.edit_text('Выберите день:', reply_markup=kb([knopki[:4], knopki[4:]]))


@dp.callback_query(F.data.startswith('d:'))
async def vybor_dnya(q: CallbackQuery):
    ch = chernoviki[q.from_user.id]
    den = q.data[2:]; ch['den'] = den
    sejchas = dt.datetime.now()
    svobodno = [f'{h:02d}:00' for h in RABOCHIE_CHASY
                if f'{h:02d}:00' not in zanyato(ch['master'], den)
                and dt.datetime.fromisoformat(f'{den} {h:02d}:00') > sejchas + dt.timedelta(hours=1)]
    if not svobodno:
        await q.answer('В этот день свободных окон нет — выберите другой', show_alert=True); return
    knopki = [(t, f't:{t}') for t in svobodno]
    await q.message.edit_text('Свободное время:', reply_markup=kb([knopki[i:i + 4] for i in range(0, len(knopki), 4)]))


@dp.callback_query(F.data.startswith('t:'))
async def vybor_vremeni(q: CallbackQuery):
    chernoviki[q.from_user.id]['vremya'] = q.data[2:]
    await q.message.edit_text('Осталось оставить телефон, чтобы мастер мог связаться.')
    await q.message.answer('Нажмите кнопку ниже 👇 или напишите номер сообщением', reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='Отправить номер', request_contact=True)]], resize_keyboard=True, one_time_keyboard=True))


@dp.message(F.contact | F.text.regexp(r'^\+?[\d\s()-]{10,18}$'))
async def telefon(m: Message):
    nomer = m.contact.phone_number if m.contact else m.text.strip()
    ch = chernoviki.pop(m.from_user.id, None)
    if not ch or 'vremya' not in ch:
        await m.answer('Начнём сначала: /start', reply_markup=ReplyKeyboardRemove()); return
    kogda = f"{ch['den']} {ch['vremya']}"
    if ch['vremya'] in zanyato(ch['master'], ch['den']):
        await m.answer('Это время только что заняли — выберите другое: /start', reply_markup=ReplyKeyboardRemove()); return
    with db() as c:
        cur = c.execute('insert into zapisi (user_id, imya, telefon, usluga, master, kogda) values (?,?,?,?,?,?)',
                        (m.from_user.id, m.from_user.full_name, nomer, ch['usluga'], ch['master'], kogda))
    u, m_ = USLUGI[ch['usluga']][0], MASTERA[ch['master']][0]
    d = dt.datetime.fromisoformat(kogda)
    await m.answer(f'Готово! Вы записаны: {u}, мастер {m_}, {DNI[d.weekday()]} {d:%d.%m в %H:%M}.\n'
                   f'Напомню за 2 часа. Отменить или перенести — кнопка «Мои записи» в /start.',
                   reply_markup=ReplyKeyboardRemove())
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f'🆕 Запись №{cur.lastrowid}: {u} у {m_}, {d:%d.%m %H:%M}\n'
                                         f'{m.from_user.full_name}, {nomer}')


@dp.callback_query(F.data == 'moi')
async def moi(q: CallbackQuery):
    with db() as c:
        rows = c.execute('select id, usluga, master, kogda from zapisi where user_id=? and otmenena=0 and kogda>=? order by kogda',
                         (q.from_user.id, dt.datetime.now().isoformat(' ', 'minutes'))).fetchall()
    if not rows:
        await q.answer('Активных записей нет', show_alert=True); return
    await q.message.edit_text('Ваши записи:', reply_markup=kb(
        [[(f'❌ {USLUGI[u][0]}, {MASTERA[ms][0]}, {k[8:10]}.{k[5:7]} {k[11:]}', f'x:{i}')] for i, u, ms, k in rows]))


@dp.callback_query(F.data.startswith('x:'))
async def otmena(q: CallbackQuery):
    with db() as c:
        c.execute('update zapisi set otmenena=1 where id=? and user_id=?', (int(q.data[2:]), q.from_user.id))
    await q.message.edit_text('Запись отменена. Записаться снова — /start')
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f'❌ Клиент отменил запись №{q.data[2:]}')


@dp.message(Command('zapisi'))
async def spisok(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    s, z = dt.date.today().isoformat(), (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with db() as c:
        rows = c.execute("select kogda, usluga, master, imya, telefon from zapisi where otmenena=0 and (kogda like ? or kogda like ?) order by kogda",
                         (s + '%', z + '%')).fetchall()
    tekst = '\n'.join(f'{k[8:10]}.{k[5:7]} {k[11:]} — {USLUGI[u][0]}, {MASTERA[ms][0]}: {i}, {t}' for k, u, ms, i, t in rows)
    await m.answer('Записи на сегодня и завтра:\n' + (tekst or 'пока пусто'))


async def napominaniya():
    while True:
        cherez2 = (dt.datetime.now() + dt.timedelta(hours=2)).isoformat(' ', 'minutes')
        with db() as c:
            rows = c.execute('select id, user_id, usluga, master, kogda from zapisi where otmenena=0 and napomnil=0 and kogda<=? and kogda>=?',
                             (cherez2, dt.datetime.now().isoformat(' ', 'minutes'))).fetchall()
            for i, uid, u, ms, k in rows:
                await bot.send_message(uid, f'⏰ Напоминаю: сегодня в {k[11:]} — {USLUGI[u][0]}, мастер {MASTERA[ms][0]}. Ждём вас!')
                c.execute('update zapisi set napomnil=1 where id=?', (i,))
        await asyncio.sleep(60)


async def main():
    asyncio.create_task(napominaniya())
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())

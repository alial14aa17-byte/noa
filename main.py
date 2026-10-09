import os, json, secrets, uuid, datetime as dt, pathlib, mimetypes, urllib.request, io, zipfile, shutil, tempfile, hmac
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker
from passlib.context import CryptContext
from apscheduler.schedulers.background import BackgroundScheduler

BASE=pathlib.Path(__file__).parent
UPLOAD=pathlib.Path(os.getenv('UPLOAD_DIR',str(BASE/'uploads'))); UPLOAD.mkdir(parents=True,exist_ok=True)
dburl=os.getenv('DATABASE_URL','sqlite:///'+str(BASE/'noa.db'))
if dburl.startswith('postgres://'): dburl='postgresql+psycopg://'+dburl[len('postgres://'):]
if dburl.startswith('postgresql://'): dburl='postgresql+psycopg://'+dburl[len('postgresql://'):]
engine=create_engine(dburl, pool_pre_ping=True, connect_args={'check_same_thread':False} if dburl.startswith('sqlite') else {})
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base(); pwd=CryptContext(schemes=['bcrypt'],deprecated='auto')
now=lambda:dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
class User(Base):
 __tablename__='users'; id=Column(Integer,primary_key=True); name=Column(String(120)); email=Column(String(255),unique=True); username=Column(String(120),unique=True,nullable=True); password=Column(String(255)); role=Column(String(20),default='admin'); active=Column(Boolean,default=True); created=Column(DateTime,default=now)
class Category(Base):
 __tablename__='categories'; id=Column(Integer,primary_key=True); name=Column(String(150)); active=Column(Boolean,default=True)
class Product(Base):
 __tablename__='products'; id=Column(Integer,primary_key=True); category_id=Column(Integer,ForeignKey('categories.id')); name=Column(String(200)); price=Column(Integer,default=0); colors=Column(Text,default='أسود,أبيض'); sizes=Column(Text,default='S,M,L,XL,2XL,3XL'); image=Column(String(255),nullable=True); active=Column(Boolean,default=True)
class Addon(Base):
 __tablename__='addons'; id=Column(Integer,primary_key=True); name=Column(String(200)); price=Column(Integer,default=0); active=Column(Boolean,default=True)
class Order(Base):
 __tablename__='orders'; id=Column(Integer,primary_key=True); code=Column(String(40),unique=True); admin_id=Column(Integer,ForeignKey('users.id')); product_id=Column(Integer,ForeignKey('products.id')); product_name=Column(String(200)); product_price=Column(Integer,default=0); color=Column(String(80)); size=Column(String(30)); qty=Column(Integer,default=1); customer=Column(String(160)); phone=Column(String(40)); governorate=Column(String(100)); area=Column(String(150)); address=Column(Text); landmark=Column(Text); notes=Column(Text); design_notes=Column(Text); images=Column(Text,default='[]'); addons=Column(Text,default='[]'); addon_total=Column(Integer,default=0); shipping=Column(Integer,default=5000); total=Column(Integer,default=0); payment_method=Column(String(30),default='cod'); paid=Column(Integer,default=0); status=Column(String(40),default='تم الرفع'); status_at=Column(DateTime,default=now); created=Column(DateTime,default=now); commission=Column(Integer,default=1500); settled=Column(Boolean,default=False)
class Setting(Base):
 __tablename__='settings'; key=Column(String(80),primary_key=True); value=Column(Text)
class Ledger(Base):
 __tablename__='ledger'; id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id')); amount=Column(Integer); note=Column(Text); created=Column(DateTime,default=now)
class History(Base):
 __tablename__='history'; id=Column(Integer,primary_key=True); order_id=Column(Integer); actor_id=Column(Integer); old=Column(String(80)); new=Column(String(80)); created=Column(DateTime,default=now)

STATUS=['تم الرفع','قيد الطباعة','تم التجهيز','عند شركة التوصيل','اليوم يصل','تم التسليم','راجع','ملغي']
ETAS={'تم الرفع':'باقي من 3 إلى 4 أيام','قيد الطباعة':'باقي من 2 إلى 3 أيام','تم التجهيز':'باقي من يوم إلى يومين','عند شركة التوصيل':'غداً يصل','اليوم يصل':'اليوم يصل','تم التسليم':'تم التسليم','راجع':'راجع','ملغي':'ملغي'}
def setting(db,key,default=''):
 r=db.get(Setting,key); return r.value if r else default
def set_setting(db,key,value):
 r=db.get(Setting,key)
 if not r: db.add(Setting(key=key,value=str(value)))
 else:r.value=str(value)
def seed():
 Base.metadata.create_all(engine)
 with Session() as db:
  owner=db.query(User).filter_by(role='owner').first()
  password=os.getenv('OWNER_PASSWORD')
  if not owner:
   if not password: print('WARNING: OWNER_PASSWORD missing. Owner login disabled until set.')
   else: db.add(User(name='المدير العام',username=os.getenv('OWNER_USERNAME','Ali'),email=os.getenv('OWNER_EMAIL','owner@example.com').lower(),password=pwd.hash(password),role='owner'))
  elif password and not pwd.verify(password,owner.password):
   # The deployment secret is authoritative: fixes old credentials without deleting data.
   owner.password=pwd.hash(password)
   print('Owner password synchronized from OWNER_PASSWORD')
  if owner:
   owner.username=os.getenv('OWNER_USERNAME','Ali')
   if os.getenv('OWNER_EMAIL'):owner.email=os.getenv('OWNER_EMAIL').lower()
  if not db.query(Category).count():
   cats={}
   for name in ['هوديات','سويترات','جاكيتات','تيشيرتات','أخرى']:
    c=Category(name=name);db.add(c);db.flush();cats[name]=c.id
   for name,price,cat in [('هودي ريكولر',33000,'هوديات'),('هودي أوفر سايز',35000,'هوديات'),('هودي درجة ثانية',28000,'هوديات'),('هودي سحاب بريميوم',37000,'جاكيتات'),('سويتر درجة ثانية',26000,'سويترات'),('سويتر قطن',0,'سويترات'),('هاف زيب',0,'سويترات'),('جاكيت بيسبول',0,'جاكيتات')]:
    db.add(Product(name=name,price=price,category_id=cats[cat],colors=('نيلي,جوزي,أزرق سماوي,أحمر,أسود' if 'سويتر' in name else 'أسود,أبيض,وردي,أحمر,نيلي,رصاصي,جوزي,زيتوني')))
  if not db.query(Addon).count():
   for n,p in [('طباعة A3 إضافية',1000),('طباعة A2 إضافية',2500),('تغليف خاص',2000),('كرت إهداء',500)]:db.add(Addon(name=n,price=p))
  if not db.get(Setting,'commission'):set_setting(db,'commission','1500')
  if not db.get(Setting,'shipping'):set_setting(db,'shipping','5000')
  db.commit()
def advance():
 with Session() as db:
  threshold=now()-dt.timedelta(hours=12)
  for o in db.query(Order).filter(Order.status=='عند شركة التوصيل',Order.status_at<=threshold).all():
   db.add(History(order_id=o.id,actor_id=None,old=o.status,new='اليوم يصل'));o.status='اليوم يصل';o.status_at=now()
  db.commit()
scheduler=BackgroundScheduler(timezone='UTC')
@asynccontextmanager
async def lifespan(app):
 seed();scheduler.add_job(advance,'interval',minutes=5,id='auto_status',replace_existing=True);scheduler.start();yield;scheduler.shutdown(wait=False)
app=FastAPI(lifespan=lifespan)
secret=os.getenv('SESSION_SECRET')
if not secret or len(secret)<32:
 raise RuntimeError('SESSION_SECRET must be set in Railway Variables (minimum 32 characters) to keep login sessions valid across workers and deployments')
app.add_middleware(SessionMiddleware,secret_key=secret,https_only=os.getenv('SECURE_COOKIES','false').lower()=='true',same_site='lax',max_age=604800)
app.mount('/static',StaticFiles(directory=str(BASE/'static')),name='static')
tpl=Jinja2Templates(directory=str(BASE/'templates'))
@app.exception_handler(401)
async def unauthenticated(request:Request,exc:HTTPException):
 if request.method=='GET' and 'text/html' in request.headers.get('accept',''):
  return go('/login')
 return JSONResponse({'detail':exc.detail},status_code=401)
tpl.env.filters['money']=lambda x:f'{int(x or 0):,}'
def auth(request,db,owner=False):
 uid=request.session.get('uid');u=db.get(User,uid) if uid else None
 if not u or not u.active:
  raise HTTPException(401,'انتهت جلسة الدخول. سجل الدخول مجدداً')
 if owner and u.role!='owner':raise HTTPException(403,'غير مصرح')
 return u
def view(request,name,**kw):return tpl.TemplateResponse(request,name,{'request':request,'statuses':STATUS,'etas':ETAS,**kw})
def go(path):return RedirectResponse(path,status_code=303)
def file_save(f):
 if not f or not f.filename:return None
 if f.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(400,'صور JPG/PNG/WEBP فقط')
 data=f.file.read(8*1024*1024+1)
 if len(data)>8*1024*1024:raise HTTPException(400,'حجم الصورة أكبر من 8MB')
 suffix={ 'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[f.content_type]
 name=uuid.uuid4().hex+suffix;(UPLOAD/name).write_bytes(data);return name
@app.get('/media/{filename}')
def media(filename:str,request:Request):
 with Session() as db:auth(request,db)
 if '/' in filename or '..' in filename:raise HTTPException(404)
 p=UPLOAD/filename
 if not p.is_file():raise HTTPException(404)
 return FileResponse(p)
@app.get('/login',response_class=HTMLResponse)
def login_page(request:Request):return view(request,'login.html')
@app.post('/login')
def login(request:Request,identity:str=Form(...),password:str=Form(...)):
 with Session() as db:
  u=db.query(User).filter((User.email==identity.strip().lower())|(User.username==identity.strip())).first()
  if not u or not u.active or not pwd.verify(password,u.password):return view(request,'login.html',error='بيانات الدخول غير صحيحة')
  request.session.clear();request.session['uid']=u.id;return go('/')
@app.post('/logout')
def logout(request:Request):request.session.clear();return go('/login')
@app.get('/',response_class=HTMLResponse)
def dashboard(request:Request):
 with Session() as db:
  try:u=auth(request,db)
  except HTTPException:return go('/login')
  q=db.query(Order) if u.role=='owner' else db.query(Order).filter_by(admin_id=u.id)
  orders=q.order_by(Order.id.desc()).limit(8).all();allorders=q.all()
  earnings=sum(o.commission for o in allorders if o.status=='تم التسليم')
  paid=sum(x.amount for x in db.query(Ledger).filter_by(user_id=u.id).all()) if u.role=='admin' else 0
  return view(request,'dashboard.html',user=u,orders=orders,categories=db.query(Category).filter_by(active=True).all(),products=db.query(Product).filter_by(active=True).all(),total=len(allorders),delivered=sum(o.status=='تم التسليم' for o in allorders),active=sum(o.status not in ['تم التسليم','راجع','ملغي'] for o in allorders),earnings=earnings,paid=paid,commission=setting(db,'commission','1500'),banner=setting(db,'admin_banner',''),headline=setting(db,'admin_headline','اختر القطعة وابدأ الطلب'),category_images={c.id:setting(db,'category_image_'+str(c.id),'') for c in db.query(Category).all()})
@app.get('/orders',response_class=HTMLResponse)
def orders_page(request:Request,status:str=''):
 with Session() as db:
  u=auth(request,db);q=db.query(Order) if u.role=='owner' else db.query(Order).filter_by(admin_id=u.id)
  if status in STATUS:q=q.filter_by(status=status)
  rows=q.order_by(Order.id.desc()).all();return view(request,'orders.html',user=u,orders=rows,selected=status)
@app.get('/orders/new',response_class=HTMLResponse)
def new_order(request:Request):
 with Session() as db:
  u=auth(request,db);return view(request,'new_order.html',user=u,products=db.query(Product).filter_by(active=True).order_by(Product.id).all(),addons=db.query(Addon).filter_by(active=True).all(),categories=db.query(Category).filter_by(active=True).all(),shipping=int(setting(db,'shipping','5000')))
@app.get('/sizes',response_class=HTMLResponse)
def size_tool(request:Request):
 with Session() as db:
  u=auth(request,db)
  return view(request,'sizes.html',user=u)
@app.get('/manage/appearance',response_class=HTMLResponse)
def appearance_page(request:Request):
 with Session() as db:
  u=auth(request,db,owner=True)
  return view(request,'appearance.html',user=u,categories=db.query(Category).all(),products=db.query(Product).all(),banner=setting(db,'admin_banner',''),headline=setting(db,'admin_headline','اختر القطعة وابدأ الطلب'))
@app.post('/manage/appearance')
def appearance_save(request:Request,headline:str=Form(''),banner:UploadFile=File(default=None)):
 with Session() as db:
  auth(request,db,owner=True)
  set_setting(db,'admin_headline',headline[:120]); filename=file_save(banner)
  if filename:set_setting(db,'admin_banner',filename)
  db.commit()
 return go('/manage/appearance')
@app.post('/manage/appearance/clear')
def clear_appearance(request:Request,key:str=Form(...)):
 with Session() as db:
  auth(request,db,owner=True)
  if key!='admin_banner' and not (key.startswith('category_image_') and key[len('category_image_'):].isdigit()):raise HTTPException(400,'مفتاح غير صالح')
  set_setting(db,key,'');db.commit()
 return go('/manage/appearance')
@app.post('/manage/category/{cid}/image')
def category_image(request:Request,cid:int,image:UploadFile=File(...)):
 with Session() as db:
  auth(request,db,owner=True);c=db.get(Category,cid)
  if not c:raise HTTPException(404)
  filename=file_save(image)
  if filename:set_setting(db,'category_image_'+str(cid),filename)
  db.commit()
 return go('/manage/appearance')
@app.post('/orders/new')
def create_order(request:Request,product_id:int=Form(...),color:str=Form(''),size:str=Form(''),qty:int=Form(1),customer:str=Form(...),phone:str=Form(...),governorate:str=Form(''),area:str=Form(''),address:str=Form(''),landmark:str=Form(''),notes:str=Form(''),design_notes:str=Form(''),shipping:int=Form(0),payment_method:str=Form('cod'),paid:int=Form(0),addons:str=Form('[]'),images:list[UploadFile]=File(default=[])):
 with Session() as db:
  u=auth(request,db);p=db.get(Product,product_id)
  if not p or not p.active:raise HTTPException(400,'منتج غير متاح')
  if not customer.strip() or not phone.strip():raise HTTPException(400,'الاسم والهاتف مطلوبان')
  if qty<1 or qty>100 or shipping<0 or paid<0:raise HTTPException(400,'قيمة غير صحيحة')
  if color and color not in p.colors.split(','):raise HTTPException(400,'لون غير متاح')
  if size and size not in p.sizes.split(','):raise HTTPException(400,'قياس غير متاح')
  try:ids=json.loads(addons)
  except:ids=[]
  chosen=db.query(Addon).filter(Addon.id.in_(ids),Addon.active==True).all() if isinstance(ids,list) else []
  extra=sum(a.price for a in chosen);total=p.price*qty+extra+shipping
  if paid>total:raise HTTPException(400,'الدفعة أكبر من المجموع')
  saved=[]
  for f in images[:15]:
   fn=file_save(f)
   if fn:saved.append(fn)
  o=Order(admin_id=u.id,product_id=p.id,product_name=p.name,product_price=p.price,color=color,size=size,qty=qty,customer=customer.strip(),phone=phone.strip(),governorate=governorate,area=area,address=address,landmark=landmark,notes=notes,design_notes=design_notes,images=json.dumps(saved),addons=json.dumps([{'name':a.name,'price':a.price} for a in chosen],ensure_ascii=False),addon_total=extra,shipping=shipping,total=total,payment_method=payment_method,paid=paid,commission=int(setting(db,'commission','1500')))
  db.add(o);db.flush();o.code=f'NOA-{o.id+1000:05d}';db.commit();send_telegram(o,u.name);return go(f'/orders/{o.id}?created=1')
def send_telegram(o,admin_name):
 token=os.getenv('TELEGRAM_BOT_TOKEN');chat=os.getenv('TELEGRAM_CHAT_ID')
 if not token or not chat:return
 try:
  import urllib.parse
  msg=f'طلب جديد {o.code}\nالأدمن: {admin_name}\nالزبون: {o.customer}\nالهاتف: {o.phone}\nالعنوان: {o.governorate} - {o.area} - {o.address}\nالمنتج: {o.product_name} | {o.color} | {o.size} | {o.qty}\nالمجموع: {o.total:,} د.ع\nالدفع: {o.payment_method} | المدفوع: {o.paid:,}\nالملاحظات: {o.notes}\nملاحظات التصميم: {o.design_notes}'
  payload=urllib.parse.urlencode({'chat_id':chat,'text':msg}).encode()
  urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',data=payload),timeout=4).read()
 except Exception as e:print('telegram notification failed',str(e))
@app.get('/tracking',response_class=HTMLResponse)
def tracking(request:Request,code:str=''):
 with Session() as db:
  u=auth(request,db)
  o=db.query(Order).filter(func.lower(Order.code)==code.strip().lower()).first() if code.strip() else None
  if o and u.role!='owner' and o.admin_id!=u.id:o=None
  history=db.query(History).filter_by(order_id=o.id).order_by(History.id.asc()).all() if o else []
  return view(request,'tracking.html',user=u,o=o,code=code,history=history,stages=STATUS[:6])

@app.get('/orders/{oid}/edit',response_class=HTMLResponse)
def edit_order_page(request:Request,oid:int):
 with Session() as db:
  u=auth(request,db);o=db.get(Order,oid)
  if not o or (u.role!='owner' and o.admin_id!=u.id):raise HTTPException(404)
  if u.role!='owner' and o.status not in ['تم الرفع','قيد الطباعة']:raise HTTPException(403,'انتهت صلاحية تعديل الطلب')
  return view(request,'edit_order.html',user=u,o=o,products=db.query(Product).filter_by(active=True).all(),images=json.loads(o.images or '[]'))

@app.post('/orders/{oid}/edit')
def edit_order_save(request:Request,oid:int,product_id:int=Form(...),color:str=Form(''),size:str=Form(''),qty:int=Form(1),customer:str=Form(...),phone:str=Form(...),governorate:str=Form(''),area:str=Form(''),address:str=Form(''),landmark:str=Form(''),notes:str=Form(''),design_notes:str=Form(''),shipping:int=Form(0),payment_method:str=Form('cod'),paid:int=Form(0),images:list[UploadFile]=File(default=[])):
 with Session() as db:
  u=auth(request,db);o=db.get(Order,oid);p=db.get(Product,product_id)
  if not o or (u.role!='owner' and o.admin_id!=u.id):raise HTTPException(404)
  if u.role!='owner' and o.status not in ['تم الرفع','قيد الطباعة']:raise HTTPException(403,'انتهت صلاحية تعديل الطلب')
  if not p or not p.active or not customer.strip() or not phone.strip() or qty<1 or qty>100 or shipping<0 or paid<0:raise HTTPException(400,'بيانات غير صحيحة')
  if color and color not in p.colors.split(','):raise HTTPException(400,'لون غير متاح')
  if size and size not in p.sizes.split(','):raise HTTPException(400,'قياس غير متاح')
  total=p.price*qty+o.addon_total+shipping
  if paid>total:raise HTTPException(400,'المدفوع أكبر من المجموع')
  existing=json.loads(o.images or '[]')
  for f in images[:max(0,15-len(existing))]:
   fn=file_save(f)
   if fn:existing.append(fn)
  for key,value in {'product_id':p.id,'product_name':p.name,'product_price':p.price,'color':color,'size':size,'qty':qty,'customer':customer.strip(),'phone':phone.strip(),'governorate':governorate,'area':area,'address':address,'landmark':landmark,'notes':notes,'design_notes':design_notes,'shipping':shipping,'payment_method':payment_method,'paid':paid,'total':total,'images':json.dumps(existing)}.items():setattr(o,key,value)
  db.add(History(order_id=o.id,actor_id=u.id,old='بيانات الطلب',new='تم تعديل التفاصيل'))
  db.commit();return go(f'/orders/{oid}')

@app.get('/orders/{oid}',response_class=HTMLResponse)
def order_detail(request:Request,oid:int,created:int=0):
 with Session() as db:
  u=auth(request,db);o=db.get(Order,oid)
  if not o or (u.role!='owner' and o.admin_id!=u.id):raise HTTPException(404)
  return view(request,'order_detail.html',user=u,o=o,created=created,images=json.loads(o.images or '[]'),addons=json.loads(o.addons or '[]'),admin=db.get(User,o.admin_id),history=db.query(History).filter_by(order_id=oid).order_by(History.id.desc()).all(),can_edit=(u.role=='owner' or o.status in ['تم الرفع','قيد الطباعة']))
@app.post('/orders/{oid}/status')
def change_status(request:Request,oid:int,status:str=Form(...)):
 with Session() as db:
  u=auth(request,db,owner=True);o=db.get(Order,oid)
  if not o or status not in STATUS:raise HTTPException(400)
  if o.status!=status:
   db.add(History(order_id=o.id,actor_id=u.id,old=o.status,new=status));o.status=status;o.status_at=now();db.commit()
  return go(f'/orders/{oid}')
@app.get('/earnings',response_class=HTMLResponse)
def earnings(request:Request):
 with Session() as db:
  u=auth(request,db);users=db.query(User).filter_by(role='admin').all() if u.role=='owner' else [u]
  rows=[]
  for a in users:
   delivered=db.query(Order).filter_by(admin_id=a.id,status='تم التسليم').all();earned=sum(o.commission for o in delivered);paid=sum(l.amount for l in db.query(Ledger).filter_by(user_id=a.id).all());rows.append({'user':a,'count':len(delivered),'earned':earned,'paid':paid,'due':earned-paid})
  return view(request,'earnings.html',user=u,rows=rows,ledger=db.query(Ledger).order_by(Ledger.id.desc()).limit(30).all() if u.role=='owner' else db.query(Ledger).filter_by(user_id=u.id).order_by(Ledger.id.desc()).all())
@app.post('/earnings/pay')
def pay(request:Request,user_id:int=Form(...),amount:int=Form(...),note:str=Form('')):
 with Session() as db:
  auth(request,db,owner=True);u=db.get(User,user_id)
  if not u or u.role!='admin' or amount<=0:raise HTTPException(400)
  earned=sum(o.commission for o in db.query(Order).filter_by(admin_id=u.id,status='تم التسليم').all());paid=sum(x.amount for x in db.query(Ledger).filter_by(user_id=u.id).all())
  if amount>earned-paid:raise HTTPException(400,'المبلغ أكبر من المستحق')
  db.add(Ledger(user_id=u.id,amount=amount,note=note));db.commit();return go('/earnings')
@app.get('/manage',response_class=HTMLResponse)
def manage(request:Request):
 with Session() as db:
  u=auth(request,db,owner=True);return view(request,'manage.html',user=u,admins=db.query(User).filter_by(role='admin').all(),categories=db.query(Category).all(),products=db.query(Product).all(),addons=db.query(Addon).all(),commission=setting(db,'commission','1500'),shipping=setting(db,'shipping','5000'))
@app.post('/manage/admin')
def add_admin(request:Request,name:str=Form(...),email:str=Form(...),password:str=Form(...)):
 with Session() as db:
  auth(request,db,owner=True)
  if len(password)<8:raise HTTPException(400,'كلمة المرور 8 أحرف على الأقل')
  if db.query(User).filter_by(email=email.lower().strip()).first():raise HTTPException(400,'الإيميل مستخدم')
  db.add(User(name=name,email=email.lower().strip(),password=pwd.hash(password),role='admin'));db.commit();return go('/manage')
@app.post('/manage/admin/{uid}/toggle')
def toggle_admin(request:Request,uid:int):
 with Session() as db:
  auth(request,db,owner=True);a=db.get(User,uid)
  if not a or a.role!='admin':raise HTTPException(404)
  a.active=not a.active;db.commit();return go('/manage')
@app.post('/manage/category')
def add_category(request:Request,name:str=Form(...)):
 with Session() as db:auth(request,db,owner=True);db.add(Category(name=name));db.commit();return go('/manage')
@app.post('/manage/category/{cid}/toggle')
def toggle_category(request:Request,cid:int):
 with Session() as db:
  auth(request,db,owner=True);r=db.get(Category,cid)
  if not r:raise HTTPException(404)
  r.active=not r.active;db.commit();return go('/manage')
@app.post('/manage/product')
def add_product(request:Request,name:str=Form(...),price:int=Form(...),category_id:int=Form(...),colors:str=Form('أسود,أبيض'),sizes:str=Form('S,M,L,XL,2XL,3XL'),image:UploadFile=File(default=None)):
 with Session() as db:
  auth(request,db,owner=True)
  if price<0 or not db.get(Category,category_id):raise HTTPException(400)
  db.add(Product(name=name,price=price,category_id=category_id,colors=colors,sizes=sizes,image=file_save(image)));db.commit();return go('/manage')
@app.post('/manage/product/{pid}/toggle')
def toggle_product(request:Request,pid:int):
 with Session() as db:
  auth(request,db,owner=True);r=db.get(Product,pid)
  if not r:raise HTTPException(404)
  r.active=not r.active;db.commit();return go('/manage')
@app.post('/manage/product/{pid}/edit')
def edit_product(request:Request,pid:int,name:str=Form(...),price:int=Form(...),colors:str=Form(...),sizes:str=Form(...),image:UploadFile=File(default=None)):
 with Session() as db:
  auth(request,db,owner=True);p=db.get(Product,pid)
  if not p or price<0:raise HTTPException(400)
  p.name=name;p.price=price;p.colors=colors;p.sizes=sizes
  if image and image.filename:p.image=file_save(image)
  db.commit();return go('/manage')
@app.post('/manage/addon')
def add_addon(request:Request,name:str=Form(...),price:int=Form(...)):
 with Session() as db:
  auth(request,db,owner=True)
  if price<0:raise HTTPException(400)
  db.add(Addon(name=name,price=price));db.commit();return go('/manage')
@app.post('/manage/addon/{aid}/toggle')
def toggle_addon(request:Request,aid:int):
 with Session() as db:
  auth(request,db,owner=True);r=db.get(Addon,aid)
  if not r:raise HTTPException(404)
  r.active=not r.active;db.commit();return go('/manage')
@app.post('/manage/settings')
def settings(request:Request,commission:int=Form(...),shipping:int=Form(...)):
 with Session() as db:
  auth(request,db,owner=True)
  if commission<0 or shipping<0:raise HTTPException(400)
  set_setting(db,'commission',commission);set_setting(db,'shipping',shipping);db.commit();return go('/manage')
@app.get('/health')
def health():return {'ok':True}

# Complete owner-only backup. Includes hashed user credentials, orders, audit trail and uploaded images.
BACKUP_MODELS=[User,Category,Product,Addon,Order,Setting,Ledger,History]
def model_records(db,model):
 return [{col.name:(getattr(row,col.name).isoformat() if isinstance(getattr(row,col.name),dt.datetime) else getattr(row,col.name)) for col in model.__table__.columns} for row in db.query(model).all()]

@app.get('/manage/backup')
def download_backup(request:Request):
 with Session() as db:
  auth(request,db,owner=True)
  payload={'format':'noa-backup-v1','created_at':now().isoformat(),'tables':{model.__tablename__:model_records(db,model) for model in BACKUP_MODELS}}
  output=io.BytesIO()
  with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
   z.writestr('data.json',json.dumps(payload,ensure_ascii=False))
   for f in UPLOAD.iterdir():
    if f.is_file() and f.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
     z.write(f,'uploads/'+f.name)
  output.seek(0)
  filename='noa-backup-'+now().strftime('%Y%m%d-%H%M%S')+'.zip'
  return StreamingResponse(output,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{filename}"','Cache-Control':'no-store'})

@app.post('/manage/restore')
async def restore_backup(request:Request,backup:UploadFile=File(...),confirm:str=Form(...)):
 with Session() as db:auth(request,db,owner=True)
 if confirm!='RESTORE':raise HTTPException(400,'اكتب RESTORE للتأكيد')
 raw=await backup.read(200*1024*1024+1)
 if len(raw)>200*1024*1024:raise HTTPException(413,'النسخة الاحتياطية أكبر من 200MB')
 try:
  z=zipfile.ZipFile(io.BytesIO(raw))
  with z:
   if len(z.infolist())>3000:raise ValueError('Too many files')
   manifest=json.loads(z.read('data.json'))
   if manifest.get('format')!='noa-backup-v1':raise ValueError('Invalid backup version')
   tables=manifest['tables']
   if set(tables)!=set(m.__tablename__ for m in BACKUP_MODELS):raise ValueError('Missing tables')
   uploads={}
   for item in z.infolist():
    if item.filename.startswith('uploads/') and not item.is_dir():
     name=item.filename[len('uploads/'):]
     if not name or '/' in name or '\\' in name or '..' in name or pathlib.Path(name).suffix.lower() not in ('.jpg','.jpeg','.png','.webp'):raise ValueError('Invalid file name')
     if item.file_size>8*1024*1024:raise ValueError('Image too large')
     uploads[name]=z.read(item)
  # Validate every table/column before touching live data.
  for model in BACKUP_MODELS:
   cols={c.name:c for c in model.__table__.columns}
   if not isinstance(tables[model.__tablename__],list):raise ValueError('Invalid table')
   for rec in tables[model.__tablename__]:
    if not isinstance(rec,dict) or set(rec)!=set(cols):raise ValueError('Invalid record columns')
  with Session() as db:
   # Transactional database replacement; errors roll back all rows.
   for model in reversed(BACKUP_MODELS):db.query(model).delete(synchronize_session=False)
   for model in BACKUP_MODELS:
    for rec in tables[model.__tablename__]:
     data=dict(rec)
     for col in model.__table__.columns:
      if isinstance(col.type,DateTime) and data[col.name]:data[col.name]=dt.datetime.fromisoformat(data[col.name])
     db.add(model(**data))
    db.flush()
   db.commit()
  for name,content in uploads.items(): (UPLOAD/name).write_bytes(content)
  request.session.clear()  # Require fresh authentication after restoring credentials.
  return go('/login')
 except (ValueError,KeyError,zipfile.BadZipFile,json.JSONDecodeError) as e:
  raise HTTPException(400,'ملف النسخة الاحتياطية غير صالح: '+str(e))

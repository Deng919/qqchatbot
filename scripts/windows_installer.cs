using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Security.Cryptography;
using System.Reflection;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Drawing;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;

class Setup : Form {
 const string Version = "0.2.0-beta.1";
 const string PayloadHash = "__PAYLOAD_SHA256__";
 TextBox program = new TextBox(), data = new TextBox(), password = new TextBox(), confirm = new TextBox();
 CheckBox shortcut = new CheckBox();
 static string Hex(byte[] b) { return BitConverter.ToString(b).Replace("-", "").ToLowerInvariant(); }
 static string RandomHex(int n) { byte[] b = new byte[n]; using(var r = RandomNumberGenerator.Create()) r.GetBytes(b); return Hex(b); }
 static string HashPassword(string value) {
  string salt = RandomHex(16); byte[] s = Encoding.UTF8.GetBytes(salt), block = new byte[s.Length+4];
  Buffer.BlockCopy(s,0,block,0,s.Length); block[block.Length-1]=1;
  using(var h = new HMACSHA256(Encoding.UTF8.GetBytes(value))) {
   byte[] u=h.ComputeHash(block), result=(byte[])u.Clone();
   for(int i=1;i<120000;i++) {u=h.ComputeHash(u); for(int j=0;j<result.Length;j++) result[j]^=u[j];}
   return "pbkdf2_sha256$120000$"+salt+"$"+Hex(result);
  }
 }
 static string Json(object o) { return new JavaScriptSerializer().Serialize(o); }
 static void CheckPath(string path) {
  for(string p=path;p!=null;p=Path.GetDirectoryName(p)) {
   if((Directory.Exists(p)||File.Exists(p)) && (File.GetAttributes(p)&FileAttributes.ReparsePoint)!=0) throw new Exception("目录不能包含符号链接或目录联接。");
  }
 }
 static bool Inside(string a,string b) {return a.Equals(b,StringComparison.OrdinalIgnoreCase)||a.StartsWith(b.TrimEnd('\\')+"\\",StringComparison.OrdinalIgnoreCase);}
 static void Install(string program, string data, string password) {
  using(var mutex=new Mutex(false,@"Global\QQDigestPublicBetaInstaller")) {
   bool acquired=false;
   try {try {acquired=mutex.WaitOne(0);} catch(AbandonedMutexException) {acquired=true;}
    if(!acquired) throw new Exception("另一个安装程序正在运行，请稍后重试。");
    InstallLocked(program,data,password);
   } finally {if(acquired) mutex.ReleaseMutex();}
  }
 }
 static void InstallLocked(string program, string data, string password) {
  if(password.Length<8) throw new Exception("登录密码至少需要 8 个字符。");
  program=Path.GetFullPath(program).TrimEnd('\\'); data=Path.GetFullPath(data).TrimEnd('\\');
  CheckPath(program); CheckPath(data);
  if(Inside(program,data)||Inside(data,program)) throw new Exception("程序目录与数据目录必须分开，不能互相包含。");
  if(Directory.Exists(program)||File.Exists(program)||Directory.Exists(data)||File.Exists(data)) throw new Exception("请选择尚不存在的新目录，安装程序不会覆盖已有文件。");
  bool madeProgram=false,madeData=false;
  try {
   using(var resource=Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip")) {
    if(resource==null) throw new Exception("安装包缺少程序文件。");
    using(var memory=new MemoryStream()) {
     resource.CopyTo(memory); memory.Position=0;
     using(var sha=SHA256.Create()) if(Hex(sha.ComputeHash(memory))!=PayloadHash) throw new Exception("安装包完整性校验失败，请重新下载。");
     memory.Position=0;
     using(var zip=new ZipArchive(memory,ZipArchiveMode.Read)) {
      foreach(var entry in zip.Entries) {
       string name=entry.FullName.Replace('/', '\\');
       if(Path.IsPathRooted(name)||name.Contains(":")||name.Split('\\').Any(x=>x=="..")||((entry.ExternalAttributes>>16)&0xF000)==0xA000) throw new Exception("安装包包含不安全的文件路径。");
       string target=Path.GetFullPath(Path.Combine(program,name));
       if(!Inside(target,program)) throw new Exception("安装包包含不安全的文件路径。");
      }
      Directory.CreateDirectory(program); madeProgram=true;
      foreach(var entry in zip.Entries) {
       string target=Path.Combine(program,entry.FullName.Replace('/', '\\'));
       if(entry.FullName.EndsWith("/")) {Directory.CreateDirectory(target);continue;}
       Directory.CreateDirectory(Path.GetDirectoryName(target));
       using(var src=entry.Open()) using(var dst=new FileStream(target,FileMode.CreateNew)) src.CopyTo(dst);
      }
     }
    }
   }
   Directory.CreateDirectory(data);madeData=true;
   var access = new DirectorySecurity();
   access.SetAccessRuleProtection(true,false);
   var ids = new[]{WindowsIdentity.GetCurrent().User,new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid,null)};
   foreach(var id in ids) access.AddAccessRule(new FileSystemAccessRule(id,FileSystemRights.FullControl,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
   Directory.SetAccessControl(data,access);
   string secret=Path.Combine(data,"session-secret.txt");
   File.WriteAllText(secret,RandomHex(32),new UTF8Encoding(false));
   var config=new Dictionary<string,object> {
    {"data_dir",data}, {"security",new {web_password_hash=HashPassword(password),session_secret_env="QQ_DIGEST_SESSION_SECRET",session_secret_file=secret,session_hours=12}},
    {"ai",new {provider_priority=new[]{"compatible"},base_url="https://api.deepseek.com",model="deepseek-flash",api_key_env="DEEPSEEK_API_KEY",api_key_file="",ui_api_key_file=Path.Combine(data,"secrets","deepseek-api-key.txt"),json_mode=true}},
    {"groups",new object[0]}, {"ntqq",new {enabled=false,qq_number=0,db_dir=""}}, {"qq_bot",new {enabled=false,app_id=""}}
   };
   string configDir=Path.Combine(data,"config");Directory.CreateDirectory(configDir);
   string configPath=Path.Combine(configDir,"config.yaml");
   File.WriteAllText(configPath,Json(config),new UTF8Encoding(false));
   using(var f=new FileStream(Path.Combine(program,"launcher.json"),FileMode.CreateNew)) {
    byte[] bytes=Encoding.UTF8.GetBytes(Json(new {config_path=configPath})); f.Write(bytes,0,bytes.Length);
   }
  } catch {if(madeData) Directory.Delete(data,true);if(madeProgram) Directory.Delete(program,true);throw;}
 }
 Setup() {
  Text="QQ Digest 公开测试版 "+Version; ClientSize=new Size(650,340);FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;
  program.Text=Directory.Exists(@"D:\")?@"D:\Apps\QQDigestDesktop-"+Version:Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","QQDigestDesktop-"+Version);
  data.Text=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"QQDigest","Data");
  string[] labels={"程序目录（新目录）","数据目录（新目录）","登录密码（至少 8 个字符）","确认登录密码"};TextBox[] boxes={program,data,password,confirm};
  for(int i=0;i<4;i++) {Controls.Add(new Label {Text=labels[i],Left=15,Top=20+i*48,Width=230});boxes[i].SetBounds(250,15+i*48,310,26);Controls.Add(boxes[i]);}
  for(int i=0;i<2;i++) {var box=boxes[i];string folder=i==0?"QQDigestDesktop-"+Version:"QQDigestData";var browse=new Button {Text="…",Left=570,Top=15+i*48,Width=50}; browse.Click+=(s,e)=>{using(var d=new FolderBrowserDialog()) {if(d.ShowDialog()==DialogResult.OK) box.Text=Path.Combine(d.SelectedPath,folder);}};Controls.Add(browse);}
  password.UseSystemPasswordChar=confirm.UseSystemPasswordChar=true;
  shortcut.Text="创建桌面快捷方式";shortcut.Checked=true;shortcut.SetBounds(20,210,250,25);Controls.Add(shortcut);
  Controls.Add(new Label {Text="需要 Windows x64 和 Microsoft Edge WebView2 Runtime。\n请使用自己的 DeepSeek API Key，安装包不包含密钥或默认密码。",Left=20,Top=245,Width=610,Height=45});
  var install=new Button {Text="安装",Left=520,Top=300,Width=100};Controls.Add(install);
  install.Click+=(s,e)=>{try {
   if(password.Text!=confirm.Text) throw new Exception("两次输入的密码不一致。");
   Install(program.Text,data.Text,password.Text);
   string warning="";
   if(shortcut.Checked) {try {CreateShortcut(Path.GetFullPath(program.Text));}catch {warning="\n快捷方式创建失败，请手动打开 QQDigestDesktop.exe。";}}
   MessageBox.Show("安装成功，请打开 QQDigestDesktop.exe 启动。\n若缺少 WebView2 Runtime，请先从 Microsoft 安装。\n反馈问题：https://github.com/Deng919/qqchatbot/issues"+warning);Close();
  }catch(Exception ex){MessageBox.Show(ex.Message,"安装失败");}};
 }
 static void CreateShortcut(string program) {
  string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"QQ Digest "+Version+".lnk");
  if(File.Exists(path)) throw new Exception("桌面快捷方式已存在。");
  Type t=Type.GetTypeFromProgID("WScript.Shell");object shell=Activator.CreateInstance(t);
  object link=t.InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{path});
  Type lt=link.GetType();lt.InvokeMember("TargetPath",BindingFlags.SetProperty,null,link,new object[]{Path.Combine(program,"QQDigestDesktop.exe")});
  lt.InvokeMember("WorkingDirectory",BindingFlags.SetProperty,null,link,new object[]{program});lt.InvokeMember("Save",BindingFlags.InvokeMethod,null,link,null);
 }
 [STAThread] static int Main(string[] args) {
  if(args.Length==3&&args[0]=="--test-install") {try {Install(args[1],args[2],Console.In.ReadLine()??"");return 0;}catch{return 1;}}
  Application.EnableVisualStyles();Application.Run(new Setup());return 0;
 }
}

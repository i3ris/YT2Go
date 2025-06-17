from flask import Flask, render_template, request, redirect, url_for, send_file, flash
import yt_dlp
import os
import uuid
import logging
import re # Düzenli ifadeler için


app = Flask(__name__)
app.secret_key = 'supersecretkey' # Flash mesajları için gerekli

# Loglama ayarı
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/start", methods=["POST"])
def start():
    url = request.form.get("url")
    format_choice = request.form.get("format") # 'format' yerine 'format_choice' kullandım karışmasın diye
    
    if not url or format_choice not in ["mp3", "mp4"]:
        flash("Geçersiz istek: URL veya format seçimi eksik/yanlış.", "error")
        return redirect(url_for("index"))

    # Geçerli bir YouTube URL'si mi kontrol et (basit bir regex ile)
    if not re.match(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$", url):
        flash("Lütfen geçerli bir YouTube URL'si girin.", "error")
        return redirect(url_for("index"))

    session_id = str(uuid.uuid4())
    
    # yt-dlp'nin dosya adını kendisi oluşturmasına izin verelim, sonra o dosyayı buluruz.
    # Bu, outtmpl'nin doğrudan dönüştürülmüş dosya adı olmasına yol açmaz.
    # Geçici bir şablon kullanacağız, sonra doğru dosyayı yakalamaya çalışacağız.
    temp_filename_pattern = os.path.join(DOWNLOAD_FOLDER, f"{session_id}_%(title)s.%(ext)s")

    ydl_opts = {
        "outtmpl": temp_filename_pattern, # Geçici dosya adı şablonu
        "noplaylist": True,
        "quiet": False, # Hata ayıklama için false kalması iyi
        "verbose": False, # Çok fazla çıktı vermemesi için false
        "logger": logger, # yt-dlp loglarını kendi logger'ımıza yönlendir
        "progress_hooks": [lambda d: logger.debug(f"İndirme durumu: {d.get('status')}, dosya: {d.get('filename')}")],
    }

    if format_choice == "mp3":
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "extract_audio": True, # Ses çıkarmayı etkinleştir
        })
    elif format_choice == "mp4":
        ydl_opts.update({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]", # MP4 için en iyi video ve sesi birleştir
            "merge_output_format": "mp4",
        })

    # FFmpeg kontrolü daha uygun bir yerde yapılmalı veya yt-dlp'nin hata mesajına güvenilmeli.
    # yt-dlp zaten FFmpeg yoksa hata verir. Bu kontrolü kaldırmak yerine,
    # hata yakalama mekanizmasını güçlendirelim.

    downloaded_filepath = None
    try:
        logger.info(f"İndirme başlıyor: URL={url}, Format={format_choice}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            
            # İndirilen dosyanın yolunu bulmaya çalışalım.
            # yt-dlp info_dict içinde `_filename` veya `requested_downloads` ile dosya yolunu verir.
            if '_filename' in info_dict:
                downloaded_filepath = info_dict['_filename']
            elif 'requested_downloads' in info_dict and info_dict['requested_downloads']:
                # Birden fazla dosya indirildiyse (örn. merge olmadan), sonuncuyu al.
                downloaded_filepath = info_dict['requested_downloads'][-1]['filepath']
            else:
                # Eğer info_dict'te yoksa, outtmpl şablonuna göre tahmin etmeye çalışabiliriz.
                # Ancak bu yöntem daha az güvenilirdir.
                # Daha iyi bir yaklaşım, yt_dlp.download() çağrısı yapmadan önce
                # bir 'progress_hook' kullanarak dosya adını yakalamaktır.
                # Şu anki haliyle, ydl.download() başarılıysa info_dict'te filename olmalı.
                pass

        if not downloaded_filepath or not os.path.exists(downloaded_filepath) or os.path.getsize(downloaded_filepath) == 0:
            logger.error(f"İndirme başarılı görünse de dosya bulunamadı veya boş: {downloaded_filepath}")
            flash("İndirme tamamlandı gibi görünüyor ancak dosya oluşturulamadı veya boş.", "error")
            return redirect(url_for("index"))
        
        logger.info(f"İndirme başarıyla tamamlandı: {downloaded_filepath}")
        
        # Orijinal filename'i korumak için, indirilmiş dosyanın adını değiştirmiyoruz.
        # download_file fonksiyonuna indirilmiş dosyanın gerçek adını gönderiyoruz.
        # Burada dosya adını almak için regex veya os.path.basename kullanacağız.
        final_filename = os.path.basename(downloaded_filepath)
        
        flash("Video/Müzik başarıyla indirildi!", "success")
        return redirect(url_for("download_file", filename=final_filename))

    except yt_dlp.utils.DownloadError as de:
        error_message = f"İndirme hatası: {str(de)}"
        logger.error(error_message)
        # FFmpeg hatasını daha spesifik yakala
        if "ffmpeg" in str(de).lower() and ("not found" in str(de).lower() or "executable not found" in str(de).lower()):
            flash("Hata: FFmpeg sisteminizde kurulu değil veya PATH'e ekli değil. Lütfen kurun.", "error")
        else:
            flash(error_message, "error")
        return redirect(url_for("index"))
    except Exception as e:
        error_message = f"Beklenmedik hata oluştu: {str(e)}"
        logger.error(error_message, exc_info=True) # exc_info ile stack trace logla
        flash(error_message, "error")
        return redirect(url_for("index"))

@app.route("/download/<filename>")
def download_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        response = send_file(filepath, as_attachment=True)
        # Yanıt gönderildikten sonra dosyayı temizle
        @response.call_on_close
        def cleanup():
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    logger.info(f"Dosya silindi: {filepath}")
            except Exception as e:
                logger.error(f"Dosya silme hatası: {str(e)}")
        return response
    else:
        logger.error(f"İndirilmek istenen dosya bulunamadı: {filepath}")
        flash("İndirilmek istenen dosya bulunamadı veya silinmiş.", "error")
        return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
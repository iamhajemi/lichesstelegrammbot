from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError
import logging
import os
import tempfile
import chess
import chess.engine
import traceback
import random
import requests
from io import BytesIO
from config import TOKEN, BOT_USERNAME
import speech_recognition as sr
from pydub import AudioSegment
import asyncio
import subprocess

# Loglama ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Aktif oyunları saklamak için sözlük
games = {}

class ChessGame:
    def __init__(self, user_id):
        self.board = chess.Board()
        self.user_id = user_id
        self.user_color = chess.WHITE
        self.current_message_id = None
        self.selected_square = None  # Seçili kare
        
        # Stockfish motorunu başlatmayı dene
        try:
            stockfish_path = os.path.join(os.path.dirname(__file__), "stockfish.exe")
            logger.debug(f"Stockfish yolu: {stockfish_path}")
            
            if os.path.exists(stockfish_path):
                self.engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
                self.engine.configure({"Threads": 2})
                logger.debug("Stockfish başarıyla başlatıldı")
            else:
                logger.warning(f"Stockfish bulunamadı: {stockfish_path}")
                self.engine = None
                
        except Exception as e:
            logger.error(f"Stockfish başlatma hatası: {str(e)}")
            logger.warning("Stockfish başlatılamadı, rastgele hamleler kullanılacak")
            self.engine = None

    def make_move(self, move_str):
        try:
            logger.debug(f"Hamle yapılıyor: {move_str}")
            
            # Hamleyi parse et
            try:
                # Önce SAN notasyonunu dene (örn: Nf3, e4, Bxe5)
                move = self.board.parse_san(move_str)
            except ValueError:
                try:
                    # SAN çalışmazsa UCI notasyonunu dene (örn: e2e4)
                    move = chess.Move.from_uci(move_str)
                except ValueError:
                    logger.error(f"Geçersiz hamle formatı: {move_str}")
                    return False, None
            
            # Hamlenin geçerli olup olmadığını kontrol et
            if move in self.board.legal_moves:
                # Hamleyi yap
                self.board.push(move)
                user_move_san = self.board.move_stack[-1].uci()
                logger.debug(f"Kullanıcı hamlesi yapıldı: {user_move_san}")
                
                # Bot'un hamlesi
                if not self.board.is_game_over() and self.board.turn != self.user_color:
                    if self.engine is not None:
                        try:
                            # En iyi hamleyi al
                            result = self.engine.play(self.board, chess.engine.Limit(time=2.0))
                            bot_move = result.move
                        except Exception as e:
                            logger.error(f"Stockfish hamle hatası: {str(e)}")
                            # Hata durumunda rastgele hamle yap
                            bot_move = random.choice(list(self.board.legal_moves))
                    else:
                        # Stockfish yoksa rastgele hamle yap
                        bot_move = random.choice(list(self.board.legal_moves))
                        
                    self.board.push(bot_move)
                    bot_move_san = self.board.move_stack[-1].uci()
                    logger.debug(f"Bot hamlesi yapıldı: {bot_move_san}")
                    return True, bot_move_san
                return True, None
                
            logger.debug(f"Geçersiz hamle: {move_str}")
            return False, None
            
        except Exception as e:
            logger.error(f"Hamle yapılırken beklenmeyen hata: {str(e)}")
            logger.error(traceback.format_exc())
            return False, None

    def __del__(self):
        # Engine'i temizle
        if hasattr(self, 'engine') and self.engine is not None:
            try:
                self.engine.quit()
            except:
                pass

    def get_board_image(self):
        try:
            # FEN notasyonunu al
            fen = self.board.fen()
            logger.debug(f"FEN alındı: {fen}")
            
            # Lichess API'sini kullan - daha büyük tahta için size parametresi ekle
            url = 'https://lichess1.org/export/fen.gif'
            params = {'fen': fen, 'size': 8}  # Tahta boyutunu büyüt
            logger.debug(f"Lichess API çağrılıyor: {url} - Params: {params}")
            
            response = requests.get(url, params=params, stream=True)
            response.raise_for_status()
            logger.debug("Lichess API yanıt verdi")
            
            # Resmi BytesIO nesnesine kaydet
            image_data = BytesIO(response.content)
            image_data.seek(0)
            logger.debug("Resim verisi hazırlandı")
            
            return image_data
        except requests.RequestException as e:
            logger.error(f"Lichess API hatası: {str(e)}")
            raise e
        except Exception as e:
            logger.error(f"Tahta resmi oluşturulurken hata: {str(e)}")
            logger.error(traceback.format_exc())
            raise e

    def get_status(self):
        try:
            if self.board.is_checkmate():
                winner = "Beyaz" if self.board.turn == chess.BLACK else "Siyah"
                return f"♔ Şah Mat! {winner} kazandı!"
            elif self.board.is_stalemate():
                return "⭐ Pat! Oyun berabere bitti."
            elif self.board.is_insufficient_material():
                return "⭐ Yetersiz materyal! Oyun berabere bitti."
            elif self.board.is_check():
                return "⚠️ Şah!"
            else:
                return "⏳ Sıra sizde (Beyaz)." if self.board.turn == chess.WHITE else "🤖 Bot düşünüyor (Siyah)..."
        except Exception as e:
            logger.error(f"Oyun durumu alınırken hata: {str(e)}")
            logger.error(traceback.format_exc())
            return "Oyun durumu belirlenemedi."

    def create_board_keyboard(self):
        keyboard = []
        files = 'abcdefgh'
        
        # 8x8 grid oluştur - minimal butonlar
        for rank in range(7, -1, -1):  # 8->1
            row = []
            for file in range(8):  # a->h
                square_name = files[file] + str(rank + 1)
                # Zero-width space karakteri kullan
                button_text = "\u200c"  # ZERO WIDTH NON-JOINER
                row.append(InlineKeyboardButton(button_text, callback_data=f"square_{square_name}"))
            keyboard.append(row)
            
        return InlineKeyboardMarkup(keyboard)

# Komut işleyicileri
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        welcome_message = f'''Merhaba {user.first_name}! Ben bir satranç botuyum.

Komutlar:
/start - Botu başlat
/help - Yardım menüsünü göster
/newgame - Yeni oyun başlat
/move e2e4 - Hamle yap (örnek: e2'den e4'e)

İyi oyunlar! ♟️'''
        await update.message.reply_text(welcome_message)
    except Exception as e:
        logger.error(f"Start komutunda hata: {str(e)}")
        await update.message.reply_text('Bir hata oluştu. Lütfen daha sonra tekrar deneyin.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
♟️ Satranç Botu - Yardım

Komutlar:
/start - Botu başlat
/help - Bu yardım mesajını göster
/newgame - Yeni oyun başlat
/move <hamle> - Hamle yap

Hamle Formatları:
1. Standart Notasyon (SAN):
   • Piyon: e4, d5
   • At: Nf3, Nc6
   • Fil: Bc4, Be7
   • Kale: Ra1, Rd8
   • Vezir: Qd1, Qh4
   • Şah: Ke2, Kg8
   • Rok: O-O (kısa rok), O-O-O (uzun rok)
   • Alma: Bxe5, Nxf3
   
2. Koordinat Notasyonu (UCI):
   • e2e4 (e2'den e4'e)
   • g1f3 (g1'den f3'e)

Örnekler:
/move e4    (e2-e4 piyonu)
/move Nf3   (g1-f3 atı)
/move Bxe5  (fil e5'teki taşı alır)
/move O-O   (kısa rok)

Not:
- Beyaz taşlarla oynarsınız
- Bot siyah taşlarla oynar
"""
    try:
        await update.message.reply_text(help_text)
    except Exception as e:
        logger.error(f"Help komutunda hata: {str(e)}")
        await update.message.reply_text('Bir hata oluştu. Lütfen daha sonra tekrar deneyin.')

async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        logger.debug(f"Yeni oyun başlatılıyor. Kullanıcı ID: {user_id}")
        
        # Yeni oyun oluştur
        games[user_id] = ChessGame(user_id)
        game = games[user_id]
        logger.debug("Oyun nesnesi oluşturuldu")
        
        try:
            # Tahtayı oluştur
            board_image = game.get_board_image()
            logger.debug("Tahta resmi alındı")
            
            # Klavyeyi oluştur
            keyboard = game.create_board_keyboard()
            logger.debug("Klavye oluşturuldu")
            
            # Mesajı gönder
            message = await update.message.reply_photo(
                photo=board_image,
                caption="Yeni oyun başladı! Beyaz taşlarla oynuyorsunuz.\nTaşları hareket ettirmek için tahtanın üzerinde tıklayın.",
                reply_markup=keyboard
            )
            logger.debug("Mesaj gönderildi")
            
            game.current_message_id = message.message_id
            logger.debug(f"Oyun başarıyla başlatıldı. Mesaj ID: {message.message_id}")
            
        except TelegramError as e:
            logger.error(f"Telegram API hatası: {str(e)}")
            await update.message.reply_text('Telegram ile iletişim hatası oluştu. Lütfen tekrar deneyin.')
            return
            
    except Exception as e:
        logger.error(f"Yeni oyun başlatma hatası: {str(e)}")
        logger.error(traceback.format_exc())
        await update.message.reply_text('Oyun başlatılırken bir hata oluştu. Lütfen tekrar deneyin.')

async def handle_square_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        
        if user_id not in games:
            await query.answer("Aktif oyun bulunamadı. Yeni oyun başlatmak için /newgame yazın.")
            return
            
        game = games[user_id]
        data = query.data.split('_')[1]  # square_e2 -> e2
        square = chess.parse_square(data)
        
        # Eğer bir kare seçili değilse ve seçilen karede taş varsa
        if game.selected_square is None:
            piece = game.board.piece_at(square)
            if piece and piece.color == game.user_color:
                game.selected_square = square
                await query.answer(f"Taş seçildi: {data}")
            else:
                await query.answer("Bu karede hareket ettirebileceğiniz bir taş yok!")
                return
        else:
            # Eğer aynı kareye tıklanırsa seçimi iptal et
            if square == game.selected_square:
                game.selected_square = None
                await query.answer("Seçim iptal edildi")
            else:
                # Hamleyi yap
                move = chess.Move(game.selected_square, square)
                if move in game.board.legal_moves:
                    success, bot_move = game.make_move(move.uci())
                    game.selected_square = None
                    
                    if success:
                        status = game.get_status()
                        move_info = f"Son hamleler:\n👤 Beyaz: {move.uci()}"
                        if bot_move:
                            move_info += f"\n🤖 Siyah: {bot_move}"
                        
                        # Yeni tahtayı gönder
                        board_image = game.get_board_image()
                        new_message = await query.message.reply_photo(
                            photo=board_image,
                            caption=f"{move_info}\n\n{status}",
                            reply_markup=game.create_board_keyboard()
                        )
                        
                        # Eski mesajı sil
                        try:
                            await query.message.delete()
                        except Exception as e:
                            logger.error(f"Eski mesaj silinirken hata: {str(e)}")
                            
                        game.current_message_id = new_message.message_id
                        
                        if game.board.is_game_over():
                            games.pop(user_id)
                    else:
                        await query.answer("Geçersiz hamle!")
                else:
                    await query.answer("Bu hamle yapılamaz!")
                    game.selected_square = None
        
    except Exception as e:
        logger.error(f"Kare seçiminde hata: {str(e)}")
        logger.error(traceback.format_exc())
        await query.answer("Bir hata oluştu!")

# Mesaj işleyici
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.lower()
        
        if 'satranç' in text or 'chess' in text:
            await update.message.reply_text('Yeni bir oyun başlatmak için /newgame komutunu kullanın.')
        else:
            await update.message.reply_text('Komutlar için /help yazabilirsiniz.')
            
    except Exception as e:
        logger.error(f"Mesaj işlemede hata: {str(e)}")
        await update.message.reply_text('Mesajınızı işlerken bir hata oluştu.')

# Hata işleyici
async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f'Update {update} caused error {context.error}')
    if update and update.effective_message:
        await update.effective_message.reply_text('Üzgünüm, bir hata oluştu.')

def main():
    logger.info('Bot başlatılıyor...')
    
    try:
        app = Application.builder().token(TOKEN).build()

        # Komutlar
        app.add_handler(CommandHandler('start', start_command))
        app.add_handler(CommandHandler('help', help_command))
        app.add_handler(CommandHandler('newgame', newgame_command))
        
        # Sesli mesaj işleyici
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))
        
        # Mesajlar
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Hatalar
        app.add_error_handler(error)

        # Bot'u başlat
        logger.info('Bot çalışıyor...')
        app.run_polling(poll_interval=3)
    except Exception as e:
        logger.error(f"Bot başlatılırken hata oluştu: {str(e)}")

def convert_voice_to_move(voice_text):
    """Ses tanıma metnini satranç hamlesine çevirir"""
    try:
        # Metni küçük harfe çevir ve gereksiz karakterleri temizle
        text = voice_text.lower().strip()
        logger.debug(f"Orijinal metin: {text}")
        
        # Türkçe karakterleri düzelt
        text = text.replace('ş', 's').replace('ı', 'i').replace('ğ', 'g')
        text = text.replace('ü', 'u').replace('ö', 'o').replace('ç', 'c')
        
        # Taş isimlerini kontrol et
        pieces = {
            'at': 'N', 'knight': 'N',
            'fil': 'B', 'bishop': 'B',
            'kale': 'R', 'rook': 'R',
            'vezir': 'Q', 'queen': 'Q',
            'sah': 'K', 'king': 'K',
            'şah': 'K'  # Hem 'sah' hem 'şah' için destek
        }
        
        # Özel hamleleri kontrol et
        if 'kısa rok' in text or 'kisa rok' in text or 'o-o' in text:
            return 'O-O'
        if 'uzun rok' in text or 'o-o-o' in text:
            return 'O-O-O'
        
        # Taş ismi var mı kontrol et
        piece = None
        for tr, en in pieces.items():
            if tr in text:
                piece = en
                text = text.replace(tr, '')  # Taş ismini metinden çıkar
                break
        
        # Gereksiz kelimeleri temizle
        text = ' '.join(text.split())  # Çoklu boşlukları tekli boşluğa çevir
        
        # Koordinatları bul
        coords = []
        for word in text.split():
            # Sadece harfleri ve rakamları al
            clean_word = ''.join(c for c in word if c.isalnum())
            
            # Eğer kelime kare koordinatı formatındaysa
            if len(clean_word) == 2 and clean_word[0] in 'abcdefgh' and clean_word[1] in '12345678':
                coords.append(clean_word)
            # 4 karakterli hamle kontrolü (e2e4 gibi)
            elif len(clean_word) == 4 and clean_word[0] in 'abcdefgh' and clean_word[1] in '12345678' and clean_word[2] in 'abcdefgh' and clean_word[3] in '12345678':
                return clean_word  # Direkt olarak hamleyi döndür
        
        # Hamleyi oluştur
        if len(coords) == 1:  # Tek koordinat (e4 veya Ke2 gibi)
            if piece:  # Taş hamlesi
                move = piece + coords[0]
            else:  # Piyon hamlesi
                coord = coords[0]
                piece_rank = '2' if coord[1] in '34' else '7'  # 3. veya 4. sıraya gidiyorsa 2. sıradan başla
                move = coord[0] + piece_rank + coord
        elif len(coords) == 2:  # İki koordinat (e2e4 gibi)
            move = coords[0] + coords[1]
        else:
            logger.debug(f"Geçersiz koordinat sayısı: {coords}")
            return None
            
        logger.debug(f"Oluşturulan hamle: {move}")
        return move
        
    except Exception as e:
        logger.error(f"Hamle çevirme hatası: {str(e)}")
        return None

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        if user_id not in games:
            await update.message.reply_text('Önce yeni bir oyun başlatın: /newgame')
            return
            
        game = games[user_id]
        
        # Ses dosyasını al
        voice = update.message.voice or update.message.audio
        if not voice:
            await update.message.reply_text('Ses mesajı alınamadı.')
            return
            
        # Geçici dosya yolları
        temp_dir = tempfile.gettempdir()
        ogg_path = os.path.join(temp_dir, f"voice_{user_id}.ogg")
        wav_path = os.path.join(temp_dir, f"voice_{user_id}.wav")
        
        try:
            # Ses dosyasını indir
            file = await context.bot.get_file(voice.file_id)
            await file.download_to_drive(ogg_path)
            logger.debug("Ses dosyası indirildi")
            
            # FFmpeg ile OGG'dan WAV'a dönüştür
            try:
                ffmpeg_path = r"C:\ffmpeg\bin\ffmpeg.exe"  # FFmpeg yolu
                command = [
                    ffmpeg_path,
                    '-i', ogg_path,
                    '-acodec', 'pcm_s16le',
                    '-ac', '1',
                    '-ar', '16000',
                    wav_path
                ]
                subprocess.run(command, check=True, capture_output=True)
                logger.debug("Ses dosyası WAV formatına dönüştürüldü")
            except subprocess.CalledProcessError as e:
                logger.error(f"FFmpeg hatası: {e.stderr.decode()}")
                await update.message.reply_text('Ses dönüşümü yapılamadı. Lütfen tekrar deneyin.')
                return
            except FileNotFoundError:
                logger.error("FFmpeg bulunamadı")
                await update.message.reply_text('Ses dönüşümü için gerekli yazılım bulunamadı.')
                return
            
            # Speech recognition
            recognizer = sr.Recognizer()
            
            # Ses seviyesi ayarları
            recognizer.energy_threshold = 50
            recognizer.dynamic_energy_threshold = True
            recognizer.pause_threshold = 0.3
            
            voice_text = None
            # Önce Türkçe dene
            try:
                with sr.AudioFile(wav_path) as source:
                    audio = recognizer.record(source)
                    voice_text = recognizer.recognize_google(audio, language='tr-TR')
                    logger.debug(f"Türkçe ses tanıma sonucu: {voice_text}")
            except:
                # Türkçe başarısız olursa İngilizce dene
                try:
                    with sr.AudioFile(wav_path) as source:
                        audio = recognizer.record(source)
                        voice_text = recognizer.recognize_google(audio, language='en-US')
                        logger.debug(f"İngilizce ses tanıma sonucu: {voice_text}")
                except:
                    await update.message.reply_text('Ses anlaşılamadı. Lütfen daha net konuşun ve gürültüsüz bir ortamda deneyin.')
                    return
            
            # Metni hamleye çevir
            move_text = convert_voice_to_move(voice_text)
            logger.debug(f"Hamleye çevrildi: {move_text}")
            
            if move_text is None:
                await update.message.reply_text(f'Algılanan ses: "{voice_text}"\nHamle anlaşılamadı. Lütfen sadece hamleyi söyleyin (örnek: e4, Nf3)')
                return
            
            # Hamleyi yap
            success, bot_move = game.make_move(move_text)
            
            if success:
                status = game.get_status()
                move_info = f"🎤 Algılanan ses: {voice_text}\n\nSon hamleler:\n👤 Beyaz: {move_text}"
                if bot_move:
                    move_info += f"\n🤖 Siyah: {bot_move}"
                
                # Yeni tahtayı gönder
                board_image = game.get_board_image()
                message = await update.message.reply_photo(
                    photo=board_image,
                    caption=f"{move_info}\n\n{status}"
                )
                
                # Eski mesajı sil
                if game.current_message_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=game.current_message_id
                        )
                    except Exception as e:
                        logger.error(f"Eski mesaj silinirken hata: {str(e)}")
                
                game.current_message_id = message.message_id
                
                if game.board.is_game_over():
                    games.pop(user_id)
            else:
                await update.message.reply_text(f'Algılanan ses: "{voice_text}"\nGeçersiz hamle! Lütfen tekrar deneyin.')
                
        except sr.UnknownValueError:
            await update.message.reply_text('Ses anlaşılamadı. Lütfen daha net konuşun ve gürültüsüz bir ortamda deneyin.')
        except sr.RequestError as e:
            await update.message.reply_text('Ses tanıma servisi şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin.')
        finally:
            # Geçici dosyaları temizle
            try:
                if os.path.exists(ogg_path):
                    os.remove(ogg_path)
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except Exception as e:
                logger.error(f"Dosya temizleme hatası: {str(e)}")
            
    except Exception as e:
        logger.error(f"Ses mesajı işlenirken hata: {str(e)}")
        logger.error(traceback.format_exc())
        await update.message.reply_text('Ses mesajı işlenirken bir hata oluştu. Lütfen tekrar deneyin.')

if __name__ == '__main__':
    main() 
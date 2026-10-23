# Boostache

Application de bureau Windows légère qui vit dans le **system tray**. Elle regroupe un chat LLM local (via Ollama), des terminaux intégrés, un éditeur de notes, un historique du presse-papiers, des captures d'écran annotables, un enregistreur du son du PC avec transcription en texte et compte rendu, une fiche complète de n'importe quel fichier ou dossier (avec les applications qui empêchent de le supprimer, un résumé et des questions à Ollama), une calculatrice Python, WhatsApp Web et Gmail avec un assistant de réponse, un assistant qui répond aux questions sur vos e-mails et messages et annonce à voix haute les nouveaux, un planificateur de tâches, un gestionnaire de raccourcis clavier globaux et une section pour gérer Ollama (modèles, téléchargements, options), une recherche dans toutes les sections (Ctrl+K) et des rappels dits à voix haute — le tout dans une fenêtre sombre et moderne, toujours au premier plan.

L'interface est une page web (HTML/CSS/JS) affichée par WebView2 via [pywebview] ; toute la logique reste en Python.

---

## Fonctionnalités

### Conversations (chat LLM)
- Conversations multi-onglets avec n'importe quel modèle Ollama installé
- Streaming des réponses token par token, bouton **Arrêter** pendant la génération
- Réflexion des modèles « thinking » (gemma, qwen3, deepseek-r1…) affichée en direct dans un bloc repliable
- Rendu Markdown complet : titres, listes, tableaux, citations, liens, blocs de code avec coloration syntaxique et bouton **Copier**
- Choix du modèle par onglet (sélecteur dans la zone de saisie ou clic droit)
- Heure de chaque question (envoi) et de chaque réponse (fin de la génération), date complète au survol ; séparateur de jour (« Hier », « Lundi 5 octobre »…) quand une conversation s'étale sur plusieurs jours ; heures reprises dans **Copier toute la conversation**
- Pièces jointes : texte/code et images (bouton trombone, collage presse-papiers, capture d'écran, glisser-déposer), avec aperçu
- Copier un message envoyé avec ses images (pleine résolution), ou une réponse en Markdown + mise en forme (collée telle quelle dans Word, Outlook…) ; clic droit sur une image jointe : copier, ouvrir dans Captures
- Clic sur une image (envoyée ou jointe) : visionneuse plein écran, clic sur l'image pour la taille réelle
- **Modifier** la dernière question (pièces jointes gardées) : la réponse est regénérée
- **Fork** depuis n'importe quelle question : nouvel onglet avec la conversation jusqu'à cette question et sa réponse, pour repartir dans une autre direction
- **Suggestions** au-dessus du champ, gardées quand la conversation avance : sur la dernière réponse (Résumer, Plus simplement, Un exemple, Développer), sur les pièces jointes (Décrire l'image, Lire le texte de l'image, Résumer le document) et des demandes à compléter (Corriger un texte, Rédiger un e-mail, Résumer un texte, Traduire : le « … » est sélectionné, Tab passe au suivant). Il en va de même dans toutes les sections qui dialoguent avec un modèle (Assistant, Documents, Fichiers, Calculatrice, WhatsApp, Gmail)
- Dictée vocale (Win+H) et lecture à voix haute des réponses (voir **Lecture à voix haute**)
- Pré-prompt système configurable
- Persistance automatique des conversations et restauration des onglets au redémarrage
- Présence d'Ollama surveillée : lancé après Boostache, arrêté puis relancé, il est détecté tout seul (essais de plus en plus espacés tant qu'il ne répond pas ; en ligne, test à l'affichage de la fenêtre, toutes les 30 s tant qu'elle est affichée et quand un appel échoue). Tant qu'il ne répond pas, tout ce qui a besoin de lui est masqué : onglet Conversations, aide contextuelle (Ctrl+Impr. écran rendu à Windows), « Joindre à la conversation » dans Captures, assistant de réponse de WhatsApp et Gmail, pré-prompt dans les paramètres
- **Bandeau** en haut de la fenêtre, quelle que soit la section : Ollama pas lancé (bouton **Lancer Ollama**, puis « Démarrage d'Ollama… »), pas installé (**Installer Ollama**), ou installation incomplète — le serveur répond mais son moteur `llama-server.exe` manque, ce que laisse une mise à jour d'Ollama annulée en cours de route (**Télécharger l'installateur** : le relancer répare Ollama sans toucher aux modèles). Fermé, il ne revient que si l'état d'Ollama change

### Aide contextuelle (Ctrl+Impr. écran)
- **Ctrl+Impr. écran**, n'importe où dans Windows : capture de la fenêtre active (même en partie cachée) et ouverture d'une conversation **Aide** dans Boostache
- Le texte de la fenêtre est lu par l'OCR intégré à Windows : les petits modèles locaux voient les images en basse résolution et ne pourraient pas lire un message d'erreur
- Le modèle propose 4 questions d'aide adaptées à ce qui est affiché (erreur, tâche en cours…) ; un clic sur une proposition la pose, clic droit pour la modifier avant, ou écrire sa propre question
- La question part avec la capture et le texte de la fenêtre ; la suite de la conversation garde ce contexte, les autres propositions restent disponibles (bouton **Autres propositions** pour en générer de nouvelles)
- Modèle choisi de préférence parmi ceux qui lisent les images (gemma3, gemma4, llava, qwen2.5vl…) et retenu pour les aides suivantes ; un modèle texte seul reçoit uniquement le texte de la fenêtre
- Désactivable dans les **Paramètres** (Captures) : Ctrl+Impr. écran retrouve alors son effet Windows

### Consoles
- Vrais terminaux (ConPTY + xterm.js) : couleurs, barres de progression, programmes interactifs, historique ↑/↓ du shell, Ctrl+C
- PowerShell ou Invite de commandes, au choix par console (PowerShell 7 détecté s'il est installé)
- Suivi du répertoire courant, affiché dans la barre et utilisé comme titre d'onglet
- Heure en tête de l'invite (`[14:32:05] PS C:\…>`) : celle de son affichage, donc de la fin de la commande précédente ; le prompt du profil PowerShell (oh-my-posh…) est gardé
- Changer de dossier : clic sur le chemin, ou glisser-déposer d'un dossier dessus
- Glisser-déposer de fichiers dans le terminal : insère leurs chemins
- Ouverture dans un terminal externe (Ctrl+T, Windows Terminal si présent)
- Lecture à voix haute de la dernière sortie ou de tout le contenu
- Persistance entre les sessions (répertoire, contenu, nom de l'onglet)

### Notes
- Éditeur de texte multi-onglets, un onglet = une note
- Titre automatique tiré de la première ligne tant que l'onglet n'est pas renommé
- Compteur de mots et de caractères, sauvegarde automatique
- Lecture à voix haute de la sélection ou de toute la note, dictée vocale

### Presse-papiers
- Capture automatique de chaque copie texte (notification Win32 `AddClipboardFormatListener`, sans polling)
- Recherche instantanée, aperçu, visionneuse du contenu complet
- Copier, lire, supprimer, tout effacer ; navigation au clavier (↑/↓, Entrée, Suppr)
- Contenus marqués confidentiels (gestionnaires de mots de passe) ignorés
- Historique **en mémoire uniquement** (rien sur disque), taille maximale configurable
- La vue est quittée automatiquement quand la fenêtre est masquée

### Captures
- **Impr. écran**, n'importe où dans Windows : sélection d'une zone de l'écran (outil Capture de Windows), ouverte dans un nouvel onglet — un onglet par image
- La fenêtre de Boostache reste affichée pendant la sélection (elle peut faire partie de la capture)
- Éditeur façon Paint : crayon, surligneur, gomme (fait réapparaître la capture d'origine), ligne, flèche, rectangle, ellipse (Maj : angles de 45°, carré, cercle), texte, remplissage, pipette, pixellisation d'une zone
- **Sélection** d'un cadre, déplaçable et redimensionnable directement sur l'image (poignées, flèches du clavier) : copier (Ctrl+C), **rogner** l'image (Ctrl+Maj+X), enregistrer, pixelliser ou effacer la zone (Suppr) ; Ctrl+A sélectionne tout
- Palette de couleurs + couleur libre, quatre épaisseurs, formes et texte pleins
- Annuler / Rétablir (Ctrl+Z, Ctrl+Y), zoom (Ctrl+molette ; Ctrl+0 ajuste l'image à la fenêtre, petites captures comprises), déplacement (Espace ou clic molette + glisser)
- Copier l'image (Ctrl+C sans sélection), **Enregistrer sous** (PNG, JPEG, WebP, BMP ; Ctrl+S), joindre à une conversation (nouvelle ou existante, au choix)
- Le bouton **+** ouvre un onglet vierge : capturer une zone, coller une image (Ctrl+V) ou en ouvrir une ; elle prend la place de l'onglet. Glisser-déposer des images fonctionne aussi
- Onglets et retouches conservés entre les sessions ; la touche Impr. écran peut être rendue à Windows dans les **Paramètres**

### WhatsApp et Gmail
- WhatsApp Web et Gmail directement dans la fenêtre (sections **WhatsApp** et **Gmail**) : QR code à scanner (WhatsApp) ou connexion Google (Gmail) une seule fois, la session est conservée entre les lancements
- Chargés à la première ouverture de leur section, puis gardés en arrière-plan : le nombre de non-lus s'affiche dans la barre latérale
- Liens des messages ouverts dans le navigateur par défaut ; Ctrl+1 à Ctrl+9 restent actifs
- Les menus, panneaux et infobulles de Boostache s'affichent par-dessus le site sans le masquer (le site est découpé à leur place) ; seule une fenêtre de dialogue le masque le temps de s'afficher
- **Assistant de réponse** (Ollama, en local — affiché tant qu'Ollama répond) sous le site ; seuls les boutons utiles sur la page affichée apparaissent (suivi en direct de la page) :
  - **Suggérer une réponse** (une discussion ou un e-mail ouvert) : trois propositions adaptées ; un clic place celle choisie dans la zone de saisie (Gmail : la réponse s'ouvre si besoin, signature et citation sont conservées)
  - **Améliorer le brouillon** (un message en cours d'écriture) : corrige et reformule le message (bouton **Rétablir l'original**)
  - Un brouillon remplacé par une proposition peut être récupéré (**Rétablir mon brouillon**)
  - Zone de saisie identique à celle des conversations (dictée vocale, modèle, lecture à voix haute) : consigne facultative (« plus formel », « décline poliment », « accepte mardi 14 h »…) ; Entrée ou le bouton d'envoi suggère une réponse (WhatsApp ; avec seulement un brouillon : l'améliore) ou pose la question (Gmail)
  - Rien n'est jamais envoyé automatiquement : il reste à relire et envoyer
- **Résumer les e-mails** (Gmail) : chaque fil de la liste affichée dans Gmail (boîte de réception, libellé, recherche… ; page affichée, 50 au plus) est lu en entier en arrière-plan — par la vue « imprimer » de Gmail, donc **sans être marqué comme lu** — puis résumé par Ollama, avec ce qu'il attend de vous (répondre, payer, confirmer…) ; une synthèse fait ensuite le point (à partir de deux e-mails), la consigne facultative s'y applique
  - Un bouton par choix, sans menu : **Résumer les N non lus**, **Résumer tous les e-mails** et, si des cases sont cochées dans Gmail, **Résumer les N sélectionnés** ; un choix sans e-mail n'est pas proposé (mêmes boutons sous l'en-tête de la liste à côté de Gmail : **Non lus**, **Tous**, **Sélection**, avec leur nombre)
  - Un e-mail ouvert : le bouton devient **Résumer cet e-mail** et ne résume que lui (tout le fil, y compris les messages repliés)
  - Les résumés s'affichent au fur et à mesure (bouton **Arrêter**) ; un clic sur un e-mail l'ouvre dans Gmail ; **Lire** (dès le premier résumé, la lecture suit les suivants ; la synthèse est lue en premier, ou à la fin si elle n'était pas encore prête), **Copier** ou **Ajouter à une note**
  - Hauteur du panneau réglable par la poignée de son bord haut (gardée d'une fois sur l'autre ; double-clic : hauteur automatique ; flèches haut/bas au clavier) — Gmail et la liste des e-mails gardent au moins 40 % de la hauteur : si la fenêtre manque de place, c'est la conversation qui rétrécit (elle défile)
  - Résumés gardés pendant la session : une relance ne refait que les nouveaux fils et ceux qui ont reçu un message, puis la synthèse
- Bouton **Recharger** (ou **Réessayer** si le site s'est arrêté)

### Enregistreur
- Enregistre **le son qui sort du PC** (musique, vidéo, appel, réunion…) directement depuis la sortie audio : la qualité ne dépend pas d'un micro, et les bruits de la pièce ne sont pas captés
- Sortie par défaut ou une sortie précise (casque, écran HDMI…) ; si la sortie change pendant l'enregistrement (casque branché), la capture reprend toute seule
- **Source** : **Son du PC** (par défaut), **PC + micro** (votre voix mêlée au son du PC, pour garder les deux côtés d'un appel) ou **Micro** seul (votre voix, une réunion autour de vous…) ; la sortie et le micro se choisissent dans le menu à côté (par défaut ceux de Windows)
- **Résultat** : **Fichier audio**, ou **Texte en direct** — aucun fichier audio, le son est transcrit par Whisper pendant l'enregistrement, découpé aux pauses de la voix : le texte s'affiche phrase par phrase sous le chronomètre (retard éventuel indiqué), avec copier et ajouter à une note ; il est écrit au fur et à mesure dans `Transcription <date>.txt`, qui rejoint la liste à l'arrêt (le son déjà reçu est transcrit jusqu'au bout). Un modèle plus précis est plus lent : sur un processeur modeste, préférer Rapide ou Équilibré pour suivre le rythme
- Pause / reprise, chronomètre et jauges de niveau (PC et micro) ; point rouge dans la barre latérale tant qu'un enregistrement tourne, même fenêtre masquée
- Fichiers en **MP3**, **M4A** (encodeur intégré à Windows, rien à installer) ou **WAV**, rangés dans `Musique\Boostache` (dossier modifiable) et nommés d'après la date
- Liste des enregistrements du dossier (et des textes en direct : voir le texte, note, conversation, renommer, supprimer) : écoute dans la fenêtre avec barre de position, renommer (F2), copier le fichier pour le coller dans un e-mail, une discussion ou un dossier (Ctrl+C), afficher dans l'explorateur, supprimer (corbeille)
- Le WAV est écrit au fil de l'eau : un arrêt brutal de l'application laisse un fichier lisible
- **Transcrire en texte** (bouton de la ligne, touche T ou clic droit) : Whisper, en local sur le processeur, rien n'est envoyé en ligne. Avancement affiché, annulable ; le texte est rangé à côté de l'audio (`nom.txt`, renommé et supprimé avec lui)
  - Fenêtre du texte : copier, ouvrir dans le Bloc-notes, lire à voix haute, ajouter à une note, joindre à une conversation (pour le résumer avec Ollama, par exemple), transcrire à nouveau ; aussi depuis le clic droit sur l'enregistrement
  - Réglages (bouton **Texte** au-dessus de la liste) : modèle **Rapide** (150 Mo), **Équilibré** (480 Mo, conseillé — environ 4 fois plus rapide que la durée de l'audio sur un processeur de bureau) ou **Précis** (1,6 Go, plus lent) ; langue parlée (détection automatique par défaut) ; transcription automatique de chaque nouvel enregistrement
  - Le modèle est téléchargé à la première transcription (connexion Internet requise une fois), dans le cache Hugging Face de l'utilisateur
- **Compte rendu** (onglet de la fenêtre du texte, ou clic droit sur l'enregistrement) : Ollama tire de la transcription l'essentiel — **en bref**, **décisions**, **actions** (qui, quoi, pour quand), **points abordés** et **questions en suspens** ; une longue transcription est lue partie par partie. Copier, ajouter à une note, refaire ; rangé à côté de l'enregistrement (`nom.compte-rendu.md`, renommé et supprimé avec lui). Modèle choisi dans la section Ollama (sinon celui des documents, de l'assistant, puis des conversations)

### Assistant
Un assistant pour Gmail et WhatsApp (les sites intégrés ci-dessus) :
- **Questions** (champ en bas, dictée vocale Win+H) : « Quels e-mails demandent une action ? », « Qui m'a écrit sur WhatsApp aujourd'hui ? », « Ai-je reçu un e-mail de ma banque ? »… Gmail et WhatsApp sont relus à chaque question (chargés en arrière-plan s'ils ne l'étaient pas) et un modèle Ollama répond au fil de l'eau, en tenant compte des questions précédentes ; sous la réponse, ce qui a été lu (« Gmail · 12 non lus, liste « Boîte de réception », 2 lus en entier »). **Lecture auto** dans la zone de saisie : chaque réponse lue à voix haute dès ses premières phrases. Copier, lire, reposer la question, retirer. Des questions toutes prêtes restent proposées au-dessus du champ, même quand des questions ont déjà été posées
  - Gmail : e-mails non lus de la boîte de réception (flux Atom de Gmail), liste affichée (ou e-mail ouvert) et, en entier, les fils dont parle la question (mots communs avec l'expéditeur, l'objet ou l'extrait)
  - WhatsApp : liste des discussions avec le dernier message de chacune, discussion ouverte et messages arrivés pendant les annonces. L'assistant n'ouvre jamais une discussion (elle serait marquée comme lue)
- **Annonces vocales** (bouton cloche) : chaque nouvel e-mail de la boîte de réception et chaque nouveau message WhatsApp est annoncé à voix haute. Discussions WhatsApp en sourdine ignorées ; option **Lire le contenu** (sinon l'expéditeur seul) ; bouton **Exemple**. Une lecture en cours n'est pas coupée : l'annonce attend sa fin (2 min au plus) et regroupe les messages arrivés entre-temps. Reprises au démarrage si elles étaient actives
  - **Autres sons** pendant une notification — son annonce, ou un message arrivé relu avec **Lire** (bouton de l'Assistant, ou Paramètres › Lecture à voix haute) : le son des autres applications ouvertes sur la sortie audio (vidéo, musique, appel… même muettes à cet instant) descend dans le mélangeur de Windows à **10 %** de son volume par défaut — ou 50 %, 25 %, coupé, ou pas du tout (Normaux) — puis remonte doucement à la fin ; chaque baisse est notée dans le Journal ; un volume changé entre-temps n'est pas touché, et si Boostache s'arrête en pleine annonce, les volumes sont remis au lancement suivant
  - Option **Résumer** (activée par défaut) : Ollama résume d'abord le message pour l'oral — l'e-mail est lu en entier (vue « imprimer » de Gmail, sans le marquer comme lu), pour WhatsApp le dernier message visible — et c'est ce résumé qui est lu, en disant si c'est urgent ou important et ce qu'on attend de vous : « E-mail urgent de EDF. Votre facture d'octobre de 84,30 euros est disponible, à régler avant le 25 octobre. À faire : payer la facture. » Le résumé, l'importance (drapeau, étoile) et l'action s'affichent aussi dans **Messages arrivés**. Sans Ollama, ou s'il échoue : l'objet de l'e-mail ou le texte du message, tel quel (« Message WhatsApp de Marie : on se retrouve à 18 h ? »)
- Sources **Gmail** et **WhatsApp** activables séparément (questions et annonces), avec leur état (chargement, surveillé, à vérifier : connexion à faire dans la section du site)
- **Messages arrivés** : la liste des messages annoncés (clic : ouvre le site, bouton Lire, bouton **Me le rappeler** : dans 1 heure, ce soir, demain matin, et la veille ou le jour de l'échéance trouvée dans le résumé — « à régler avant le 25 octobre » — ou le jour et l'heure choisis ; le rappel prévu s'affiche sur le message) ; pastille verte dans la barre latérale tant que les annonces sont actives, compteur des messages pas encore vus
- Relevés : WhatsApp toutes les 10 s, Gmail toutes les 60 s, et aussitôt que le compteur de non-lus d'un site change. Un nouveau message est repéré à la hausse des non-lus d'une discussion (WhatsApp) ou à un nouvel e-mail non lu (Gmail) ; ce qui était déjà là à l'activation n'est pas annoncé
- Rien n'est envoyé ni marqué comme lu ; rien ne quitte le PC (Ollama en local). Modèle choisi dans la section Ollama ou sous le champ de question (sinon celui de l'assistant de réponse, puis des conversations)

### Documents
Questions et résumés sur des dossiers du PC (RAG local : rien ne quitte le PC) :
- **Dossiers** : bouton **Ajouter un dossier** ou glisser-déposer un dossier ; sous-dossiers compris, sauf les dossiers cachés et techniques (`.git`, `node_modules`…). Un dossier qui contient des dossiers déjà ajoutés les remplace ; clic droit : **Exclure de l'index** (sous-dossier ou fichier) / **Inclure**, **Retirer de la liste** (efface son index, pas les fichiers)
- **Formats** : texte et code, PDF, Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`, avec les notes), OpenDocument, RTF, HTML, e-mails (`.eml`), EPUB, notebooks Jupyter. Non lus : anciens formats Office (`.doc`, `.xls`, `.ppt` — à réenregistrer), PDF scannés (sans texte), fichiers OneDrive restés en ligne (les lire les téléchargerait), fichiers de plus de 20 Mo (texte) ou 300 Mo (documents) ; un fichier très long n'est indexé que sur son début (1,5 million de caractères)
- **Arborescence** comme l'explorateur, avec l'état de l'indexation : pour chaque dossier, fichiers traités sur le total (mini-barre de progression, ✓ quand tout est indexé) ; pour chaque fichier, indexé (✓, nombre de passages en infobulle), en attente, en cours (roue), illisible (⚠, raison en infobulle), sans texte, exclu, format non pris en charge (grisé). En bas, l'avancement global (fichier en cours, temps restant estimé) ; en haut, **Indexation 45 %** / **À jour**, pause / reprise et le menu des réglages (rechercher les changements, tout réindexer, modèles). Clavier : flèches, Entrée (ouvre), Maj+F10 (menu) ; double-clic : ouvrir le fichier (un script s'ouvre dans un éditeur, sans s'exécuter)
- **Index** : chaque fichier est découpé en passages (~1 200 caractères), rangés dans une base SQLite locale avec un index plein texte (sans accents) et les **vecteurs** d'un modèle d'embeddings d'Ollama — **embeddinggemma** conseillé (622 Mo, multilingue), installable d'un clic depuis la section ; sans lui, la recherche se fait par mots seulement. Les dossiers sont **surveillés** : un fichier ajouté, modifié ou supprimé est pris en compte en quelques secondes (et nouveau parcours complet toutes les 30 min). Pendant une réponse, le calcul des vecteurs attend
- **Questions** (champ en bas, dictée vocale Win+H) : les passages les plus proches (par le sens et par les mots, classements fusionnés) sont donnés au modèle, qui répond au fil de l'eau en citant ses sources **[1]**, **[2]** — clic : le passage, mots surlignés, avec Ouvrir le fichier, Afficher dans l'explorateur, Voir dans l'arborescence. Les questions de suite (« et pour Lyon ? ») tiennent compte des précédentes. **Portée** (sous le champ) : tous les dossiers, ou le dossier ou le fichier choisi dans l'arborescence (✕ pour revenir à tous)
- **Fichier choisi** : **Résumer**, **Points clés**, **Expliquer** (le code aussi) ou lui poser une question — lu en entier s'il tient dans le contexte du modèle, sinon partie par partie (16 parties au plus, réparties dans tout le document) ; **Texte indexé** montre les passages retenus. **Dossier choisi** : **Présenter le dossier** (sujets, organisation, questions à poser). Réponses : copier, lire à voix haute, ajouter à une note, recommencer, retirer ; gardées d'une session à l'autre
- **Suggestions** au-dessus du champ, gardées après les premières questions : les actions de la portée choisie (Résumer, Points clés, Expliquer un fichier ; Présenter un dossier), la suite de la dernière réponse (Développer, Plus simplement, Citer les passages) et des questions à compléter
- **Rechercher** (champ au-dessus de l'arborescence, Entrée) : noms de fichiers et passages correspondants, sans attendre de modèle, dans la portée choisie
- Modèle des réponses choisi sous le champ de question ou dans la section Ollama (sinon celui de l'assistant, puis des conversations) ; modèle d'embeddings dans le menu des réglages (le changer recalcule les vecteurs)

### Fichiers
Tout sur un fichier ou un dossier : **glisser-déposer** (plusieurs à la fois possible) ou **Choisir un dossier** / **Choisir un fichier**. Les éléments restent dans une barre en haut (12 au plus, retrouvés au démarrage) ; clic du milieu ou × pour en retirer un.
- **Fiche** : image de l'élément comme dans l'Explorateur, type, chemin (clic : ouvrir le dossier parent dans la section), attributs (lecture seule, caché, système, compressé, chiffré, OneDrive en ligne seulement ou toujours sur l'appareil…), lien symbolique ou raccourci (clic : afficher la cible) ; **Ouvrir** (avec l'application associée), **Explorateur**, copier le chemin, **Actualiser** (F5), **Mettre à la corbeille** (après confirmation)
- **Informations** : taille et taille réellement occupée sur le disque, nombre de mots et de pages d'un document, dates de création, de modification et de dernier accès (avec « il y a 3 jours »), propriétaire, application qui l'ouvre, lecteur (système de fichiers, place libre)
- **Propriétés de Windows** : les colonnes « Détails » de l'Explorateur — auteur, titre, dimensions, durée, appareil photo, version, éditeur d'un programme…
- **Aperçu** : début d'un fichier texte ou de code (coloré), début du texte d'un document (PDF, Word, Excel, PowerPoint…), image, contenu d'une archive ZIP. Un fichier OneDrive resté en ligne n'est pas lu (il serait téléchargé)
- **Dossier** : parcours en arrière-plan (arrêtable) — taille totale, nombre de fichiers et de dossiers, **répartition par type** (barre et légende : documents, images, vidéos, audio, archives, code, programmes), ce qui pèse **le plus lourd** au premier niveau, **plus gros fichiers**, **modifiés récemment** (clic : les afficher dans la section), fichiers OneDrive en ligne seulement, éléments inaccessibles
- **Empreintes** SHA-256 et MD5 d'un fichier (clic : copier)
- **Utilisé par** : les processus qui utilisent l'élément et peuvent **empêcher de le supprimer** (« Le fichier est ouvert dans un autre programme »), en une seconde environ, avec leur icône et leurs fenêtres ; pour un dossier, tout ce qu'il contient compte. Pour chacun, ce qu'il utilise (clic : afficher dans l'explorateur) : fichier ouvert, dossier ouvert (dossier courant d'une console, surveillance…), programme lancé depuis le dossier, DLL chargée
  - **Fermer** (comme la croix de ses fenêtres : l'application peut proposer d'enregistrer) ou **Terminer** (arrêt immédiat, après confirmation) ; **Tout terminer** ; l'Explorateur se **redémarre** (barre des tâches comprise). Non proposés : processus de Windows indispensables, Boostache lui-même. Après chaque action, nouvel examen
  - Sans droits d'administrateur, les services et les applications lancées en administrateur ne peuvent pas être examinés (nombre et noms indiqués) : **Analyser en administrateur** (autorisation de Windows) ; une application lancée en administrateur se termine de même (**Terminer en administrateur**). Les processus protégés (antivirus) restent hors d'atteinte
  - Limites : un fichier projeté en mémoire sans rester ouvert (autre qu'une DLL) n'est pas détecté ; le nom d'un dossier surveillé par un programme en attente de changement (watchdog…) ne peut pas être lu tant que rien n'y change
- **Résumé** par Ollama, lancé dès que l'élément s'affiche (désactivable dans le menu du modèle) : texte d'un document (en entier, ou partie par partie s'il est long), **description d'une image** (avec un modèle qui lit les images : gemma4, qwen3.5…), **présentation d'un dossier** (d'après son contenu, ses sous-dossiers et son lisez-moi) ou d'une archive. **Recommencer** pour en refaire un
- **Questions** (champ en bas, dictée vocale) sur l'élément, avec des suggestions selon son type, toujours proposées (document : points clés, dates et montants, ce que je dois faire ; image : décrire, lire le texte ; dossier : ce qui prend de la place, fichiers récents, faire le ménage) : ses propriétés et son contenu sont donnés au modèle (pour un long document, les passages les plus proches de la question) ; les questions de suite tiennent compte des précédentes. Sur un programme ou un autre fichier illisible, seules ses propriétés sont connues. Réponses : copier, lire à voix haute (et Lecture auto), ajouter à une note, reposer, retirer
- Modèle choisi sous le champ ou dans la section Ollama (sinon celui des documents, de l'assistant, puis des conversations) ; une seule réponse à la fois (une nouvelle demande arrête la précédente)

### Calculatrice
Console Python pour les calculs, avec un assistant en conversation :
- Un calcul puis **Entrée** : le résultat s'affiche comme dans la console Python (« = 14.4 »), avec la notation française à côté pour les nombres (1 414 213,56 ; 1,5 × 10³⁰ ; ≈ 0,5 pour une fraction). Clic sur le résultat : le copier ; clic droit : reprendre dans le champ, recalculer à la suite, copier, demander à l'assistant de l'expliquer ou de corriger l'erreur, retirer
- **Modifier un calcul précédent** : clic sur son code, il devient modifiable sur place (Entrée : recalculer, Échap : annuler). Il est recalculé avec **tous ceux qui le suivent** — changer `prix = 49.9` en `prix = 59.9` met à jour chaque résultat qui en dépend (calculs mis en évidence) : un interpréteur neuf rejoue les calculs d'avant, comme si celui-ci avait été écrit ainsi dès le départ. Les variables en cours sont gardées ; un calcul d'une session passée se modifie de même. Chaque calcul porte son numéro (#12), une étincelle s'il vient de l'assistant, un crayon s'il a été modifié
- **Assistant** (en bas, Ollama, en local) : on lui demande en français — « mensualité d'un prêt de 200 000 € sur 20 ans à 3,5 % », « et sur 25 ans ? », « mets le prix à 59,90 », « combien de jours avant Noël ? », « pourquoi cette erreur ? ». Il connaît la calculatrice (fonctions prêtes, variables, derniers calculs et leurs résultats) et ne calcule jamais de tête : il lance les calculs dans la même console (ils s'ajoutent à l'historique) ou **modifie** un calcul précédent par son numéro, puis répond en notation française. Sous sa réponse, les calculs faits (clic : les voir dans l'historique). Suggestions toujours proposées au-dessus du champ (corriger l'erreur, expliquer le dernier calcul, vérifier mes calculs, demandes à compléter) ; Lecture auto, conversation repliable et gardée d'une session à l'autre. Son code ne peut ni ouvrir de fichier ni importer de module hors calcul ; un de ses calculs qui dure plus de 30 s est interrompu. Il faut un modèle qui sait utiliser des outils (qwen3.5, gemma4…), choisi sous le champ ou dans la section Ollama
- **Variables** et fonctions gardées d'un calcul à l'autre (`x = 3`, `def f(x): …`), listées à droite (clic : insérer) ; `_` ou `ans` : dernier résultat ; `print` affiché
- Prêts à l'emploi : toutes les fonctions de `math` (`sqrt`, `log`, `sin`, `factorial`, `comb`, `pi`…), `statistics` (`mean`, `median`, `stdev`…), `Fraction` et `Decimal` (calcul exact), dates (`date`, `datetime`, `timedelta`, `jour(d)` : jour de la semaine en français), `numpy` (`np`), et quelques fonctions pratiques : `ttc(ht, tva=20)`, `ht(ttc, tva=20)`, `variation(avant, après)` (en %), `pourcentage(part, total)`, `mensualite(capital, taux, années)` (prêt à taux fixe), `interets(capital, taux, années)` (intérêts composés)
- **Aide** à droite : exemples à insérer d'un clic (une sélection dans le champ devient l'argument : `sqrt(…)` autour de `2`)
- Saisie : **Maj+Entrée** nouvelle ligne, une ligne qui finit par « : » passe à la ligne suivante indentée (def, for, if…), Tab indente, **↑ ↓** rappellent les calculs précédents, Échap vide le champ ; erreurs en rouge (dernière ligne, **détails** pour la trace)
- Les calculs tournent dans un **processus Python à part** : un calcul trop long s'interrompt (bouton carré, ou **Interrompre** après 2 s) sans bloquer Boostache — ses variables sont alors effacées, comme avec **Réinitialiser**. Historique des calculs gardé d'une session à l'autre ; ceux dont les variables ont été effacées sont suivis de **Rejouer** (les recalcule pour retrouver leurs variables)

### Lecture à voix haute
- Boutons **Lire** sous chaque réponse (conversations, assistant, documents, propositions et brouillon amélioré de WhatsApp et Gmail, réponses et résumés de Gmail), des consoles, notes, du presse-papiers, des transcriptions et des messages de l'assistant ; clic droit sur un e-mail résumé ou sur une proposition de réponse : **Lire**
- **Zones de saisie** (Conversations, Assistant, Documents, WhatsApp, Gmail), à côté du bouton d'envoi :
  - Interrupteur **Lecture auto** : chaque réponse est lue à voix haute au fur et à mesure qu'elle s'écrit (propositions et brouillon amélioré : dès qu'ils arrivent) ; réglé séparément pour chaque section, activé pendant une réponse il la lit aussitôt
  - Bouton **Lire** : toutes les réponses, ou toute la conversation avec les demandes (« Demande : … Réponse : … ») — clic droit pour choisir, commun à toutes les sections ; une réponse en cours est suivie jusqu'au bout. Pendant une lecture de la conversation (une réponse, la lecture auto), il l'arrête
- **Lecture au fil de l'eau** : une réponse en cours de génération (conversation, assistant, documents, question et résumé des e-mails) se lit dès ses premières phrases, sans attendre la fin — la lecture suit le texte qui arrive (« En attente de la suite… » dans le lecteur quand elle l'a rattrapé)
- Trois voix au choix dans les **Paramètres** (même choix que le projet audio-report) :
  - **Voix naturelle** (par défaut) : voix neuronales de Microsoft, celles de « Lire à voix haute » d'Edge, via [edge-tts] — gratuites, sans clé, très expressives (13 voix françaises : France, Québec, Belgique, Suisse ; Sylvie par défaut). Le texte lu est **envoyé à Microsoft** : il faut une connexion
  - **Hors ligne** : voix neuronale [Piper], calculée sur le PC (siwis par défaut ; autres voix avec `python -m piper.download_voices fr_FR-tom-medium`, rangées dans `%USERPROFILE%\.local\share\piper\voices`)
  - **Windows** : voix SAPI installées (Hortense…), toujours disponibles
- Une voix qui ne répond pas (connexion coupée, voix absente) passe la main à la suivante — voix naturelle → hors ligne → Windows, jamais dans l'autre sens — avec une notification ; la voix naturelle n'est pas retentée pendant une minute
- Démarrage rapide même sur un long texte : découpé en phrases, la suivante est préparée pendant que la précédente est lue ; liens réduits au nom du site, émojis et séparateurs ignorés, courte pause entre les paragraphes
- **Lecteur** dans la barre latérale pendant la lecture : voix et position (phrase 3/12, phrase en cours au survol), pause / reprise, phrase précédente (ou début de la phrase en cours) et suivante, arrêt, vitesse de 0,75× à 2× (changée en cours de lecture) et **volume** (curseur ou molette, bouton du haut-parleur pour couper ; appliqué tout de suite, sans toucher au volume de Windows ni à celui des autres applications) — aussi dans les **Paramètres**
- Bouton **Écouter** pour essayer une voix avant de la choisir

### Ollama
- **Serveur** : en marche ou non (et sa version), bouton **Lancer Ollama** quand il ne répond pas (son application, sinon `ollama serve` sans fenêtre) ou **Installer Ollama** s'il manque ; adresse, carte graphique et mémoire utilisées, dossier des modèles (place prise, place libre, bouton Ouvrir). Option **Lancer Ollama avec Boostache** : s'il ne répond toujours pas 15 s après le démarrage de Boostache (arrêté, ou fermé par une mise à jour d'Ollama qui a échoué)
- **Modèles installés** : taille, paramètres, quantification, contexte maximal, capacités (images, réflexion, outils, audio, embeddings) et fonctions de Boostache qui l'utilisent ; modèles **en mémoire** (part sur la carte graphique, heure de libération) avec **Libérer** ; menu : utiliser pour une fonction, mettre à jour, copier le nom, fiche sur ollama.com, **supprimer** (après confirmation). **Vérifier les mises à jour** compare chaque modèle à sa version publiée (Ollama 0.40 et plus garde une variante par moteur de calcul : elles comptent dans la taille)
- **Télécharger un modèle** : bibliothèque d'ollama.com dans la fenêtre — suggestions pour Boostache (modèles qui lisent les images, bons en français), les plus téléchargés, recherche instantanée (nom, description, « vision »…). Filtre **Adaptés à ce PC** (par défaut) : seuls les modèles qui conversent et dont une taille tient dans la carte graphique ; chaque taille est colorée (vert : rapide, jaune : déborde sur le processeur, barré : trop gros), d'après sa taille exacte, la mémoire de la carte graphique disponible pour Ollama (lue dans son journal) et le contexte choisi ; la **variante conseillée** (la plus grande qui reste rapide) se télécharge d'un clic depuis la ligne. Un modèle déplié montre ses variantes : taille, contexte et si elle **tient dans la carte graphique**, déborde sur le processeur (plus lent) ou est trop grosse pour ce PC ; toutes les quantifications à la demande. Un nom exact (`gemma4:e4b`, `ollama run …` ou une adresse ollama.com / Hugging Face collés) se télécharge directement (Entrée)
- Téléchargements en arrière-plan : progression, débit, temps restant, **annulation** (Ollama reprend plus tard ce qui a été reçu) ; place sur le disque vérifiée avant de commencer ; pastille dans la barre latérale et notification Windows à la fin si la fenêtre est masquée
- **Options** de tous les appels de Boostache : **contexte** (4K à 64K tokens, 8K par défaut), durée pendant laquelle un modèle reste **en mémoire** après un appel (de « aussitôt » à « jamais », 5 min par défaut), **modèle de chaque fonction** (Conversations, aide contextuelle, WhatsApp et Gmail, assistant, documents, fichiers, calculatrice, enregistreur)
- **File d'attente commune** : toutes les sections passent par elle pour appeler Ollama. Ce que vous demandez (conversation, question, suggestion de réponse, résumé d'un fichier affiché…) part tout de suite ; les résumés des annonces attendent la fin de vos demandes (30 s au plus), le résumé des e-mails de Gmail passe entre deux demandes, l'index des documents en dernier — un seul de ces appels de fond à la fois. Point bleu sur « Ollama » dans la barre latérale tant qu'un appel est en cours (infobulle : lequel, combien en attente), détail dans la carte **Serveur** (en cours depuis combien de temps, en attente)
- Moteur d'Ollama arrêté net pendant un appel (« llama-server process has terminated », parfois au chargement d'un modèle) : l'appel est refait une fois, seulement s'il n'avait encore rien répondu ; c'est noté dans le Journal
- Les modèles d'embeddings (qui ne conversent pas) et les entrées internes d'Ollama ne sont pas proposés dans les listes de modèles ; « Gérer les modèles… » dans ces listes ouvre la section

### Journal, tâches et raccourcis
- **Journal** : ce que fait Boostache et surtout ce qui échoue, pour suivre un problème et le résoudre
  - Chaque entrée a un niveau — **Erreurs**, **Avertissements**, **Infos**, **Détails** (techniques, masqués par défaut) — et sa source (Gmail, Documents, Enregistreur, Interface…) ; filtres par niveau (avec leur nombre), par source et par texte
  - Une erreur rattrapée dans le code garde la **trace de l'exception** : clic sur la ligne pour la déplier. Un message répété est compté (×12) au lieu d'être répété
  - Rien ne se perd plus sans console (`pythonw.exe`) : exceptions non rattrapées (threads, callbacks Windows), messages des bibliothèques (pywebview, asyncio…), sorties stdout/stderr, appels de l'interface vers Python en échec et erreurs JavaScript de l'interface (exceptions, promesses rejetées, `console.error`)
  - Pastille rouge dans la barre latérale : erreurs arrivées depuis la dernière visite du Journal
  - **Copier le rapport** : versions (commit, Python, pywebview, WebView2, Windows), état (Ollama, mémoire, threads), erreurs et avertissements avec leurs traces et dernières entrées — à coller dans une conversation pour analyser le problème
  - Sur disque : un fichier par lancement dans le dossier `logs` des données (les 20 derniers gardés), écrit au fil de l'eau ; bouton **Fichiers du journal** : celui de la session, le dossier, les sessions précédentes (plantage ou arrêt anormal signalés)
  - Plantage natif (DLL, WebView2…) : la pile de chaque thread est relevée (faulthandler) ; au lancement suivant, une session qui ne s'est pas terminée normalement est signalée dans le Journal avec la fin de son journal. Un arrêt de Windows n'est pas compté comme anormal : Boostache accepte l'arrêt (il ne le bloque plus), enregistre, remet le son des autres applications et se ferme proprement ; un arrêt avec le démarrage rapide de Windows (qui ne remet pas l'heure de démarrage à zéro) est reconnu dans le journal Système
- **Tâches** : tâches de `tasks.py` + tâches créées depuis l'interface (intervalle ou heure fixe, code Python avec accès au `logger`), exécution manuelle
- **Rappels** (carte en haut de la section Tâches, **Nouveau rappel**, ou « Me le rappeler » sur un message arrivé) : un texte à un jour et une heure, dit à voix haute (le son des autres applications baisse, comme pour une annonce) et affiché en notification Windows ; un rappel passé pendant que Boostache était fermé est donné au lancement, « en retard ». Les rappels donnés restent 7 jours dans la liste : **Remettre à plus tard** (10 min, 1 h, demain matin… ou le moment choisi), supprimer
- **Raccourcis** : raccourcis globaux de `bindings.py` (et Impr. écran, Ctrl+Impr. écran), actifs même fenêtre masquée, déclenchables à la main

### Interface
- Barre latérale repliable, onglets renommables (double-clic ou F2), menus contextuels
- Navigation au clavier : Ctrl+1 à Ctrl+9 pour les sections, Ctrl+, pour les paramètres
- **Rechercher partout** (`Ctrl+K`, ou **Rechercher** en haut de la barre latérale) : conversations, notes, messages arrivés, transcriptions, presse-papiers, calculs, documents indexés (par les mots, sans appel à Ollama) et tâches, plus les sections et quelques actions (nouvelle conversation, nouvelle note, capture d'écran, nouveau rappel). Sans accents ni majuscules, tous les mots dans n'importe quel ordre ; extraits avec les mots trouvés ; ↑↓ puis Entrée ouvre le résultat dans sa section — le message de la conversation, le texte dans la note (sélectionné), la transcription, le calcul… — mots trouvés surlignés quelques secondes
- **Écran partagé** : deux sections côte à côte (Conversations et Consoles, par exemple) — bouton en haut de la barre latérale ou `Ctrl+Shift+S`. La barre latérale ouvre les sections dans le volet actif (souligné en haut) ; `Ctrl+clic` ou clic du milieu sur une section l'ouvre dans l'autre volet ; clic droit : « Ouvrir à gauche / à droite », ou glisser la section vers une moitié de l'écran. Séparateur redimensionnable (double-clic : parts égales ; clic droit : inverser, fermer un volet), `F6` pour passer d'un volet à l'autre ; la disposition est retrouvée au démarrage
- Fermer la fenêtre (✕) la range dans le tray (le bouton Réduire, qui ferait pareil, est grisé) ; `Ctrl+Shift+D` la rappelle
- Clic droit sur l'icône du tray : Ouvrir, **Garder au premier plan : _titre_** (garde au premier plan, ou libère, la fenêtre active juste avant le clic sur l'icône, Boostache compris ; cochée si elle l'est déjà), **Capture d'écran** (comme Impr. écran), **Démarrer / Arrêter l'enregistrement** (avec les derniers réglages de l'Enregistreur ; une notification confirme le démarrage si la fenêtre est masquée), Recharger, Quitter
- **Envoyer vers une conversation ou une note** (capture, texte d'un enregistrement) : un menu propose « Nouvelle conversation » / « Nouvelle note », présélectionnée (Entrée), puis les conversations ou notes existantes ; une conversation ou une note vide est réutilisée plutôt que d'en ouvrir une autre, et le texte envoyé à une note s'ajoute à sa fin
- Bouton **épingle** (bas de la barre latérale) : garder n'importe quelle fenêtre ouverte au premier plan, Boostache compris — chaque fenêtre avec l'icône de son application, une miniature et son titre complet ; un curseur règle sa transparence (20 à 100 %, double-clic pour la rendre opaque ; celle de Boostache est mémorisée) ; filtre au clavier et « Tout libérer »

---

## Prérequis

- Python 3.11+
- Windows 10/11 avec le runtime [WebView2] (installé d'office avec Edge ; sinon, le télécharger)
- [Ollama](https://ollama.com) installé et au moins un modèle téléchargé — depuis la section **Ollama** de Boostache, par exemple (les fonctions qui en ont besoin n'apparaissent que lorsque son serveur répond)
- Lancer en **administrateur** pour activer les hotkeys globaux dans toutes les applications

---

## Installation

```bash
git clone https://github.com/sribera-code/boostache.git
cd boostache
pip install -r requirements.txt
```

---

## Démarrage

```bash
python main.py            # démarre dans le tray
python main.py --show     # affiche la fenêtre dès le démarrage
python main.py --debug    # outils de développement web (Ctrl+Maj+clic droit → Inspecter)
```

L'application se range dans le system tray. Double-clic sur l'icône ou `Ctrl+Shift+D` pour ouvrir la fenêtre.

Pour démarrer automatiquement avec Windows, créer un raccourci vers `pythonw.exe main.py` (voir `Boostache.lnk`) dans `shell:startup`.

---

## Personnalisation

### Tâches planifiées — `tasks.py`

```python
def register():
    def ma_tache():
        logger.log("Ma tâche s'exécute.")

    task_manager.add(
        schedule.every(30).minutes.do(ma_tache),
        label="Ma tâche (toutes les 30 min)"
    )
```

`logger.log(message)` choisit le niveau tout seul (erreur avec la trace de l'exception dans un bloc `except`, avertissement pour un message d'échec, info sinon) ; `logger.debug`, `logger.info`, `logger.warning` et `logger.error` le fixent.

### Raccourcis clavier — `bindings.py`

```python
def register(open_dashboard_fn):
    def mon_action():
        logger.log("Action déclenchée.")

    hotkey_manager.add("ctrl+alt+x", mon_action, label="Mon action")
```

Après modification, clic droit sur l'icône tray → **Recharger** : Boostache relance proprement le process pour prendre en compte tous les changements (interface, tâches, raccourcis).

### Interface — `web/`

L'interface n'a pas d'étape de compilation : modifier un fichier de `web/` puis **Recharger** suffit. Les couleurs sont des variables CSS en tête de `web/css/app.css`.

---

## Structure

```
boostache/
├── main.py               # Point d'entrée + icône du tray
├── app.py                # Fenêtre pywebview, cycle de vie, état initial de l'interface
├── api.py                # Méthodes Python appelées par l'interface
├── bridge.py             # Événements Python → interface (file + regroupement)
├── chat.py               # Conversations Ollama (streaming, réflexion, pièces jointes, aide contextuelle)
├── llm.py                # Appels à Ollama des sections : file d'attente commune (priorités), nouvel essai si le moteur s'arrête
├── search.py             # Recherche dans toutes les sections (Ctrl+K)
├── reminders.py          # Rappels : échéances lues en français, voix et notification à l'heure dite
├── minutes.py            # Enregistreur : compte rendu d'une transcription (Ollama, schéma JSON)
├── ocr.py                # Texte d'une image (OCR de Windows, via PowerShell)
├── assistant.py          # Assistant : questions sur Gmail et WhatsApp (Ollama), annonce vocale des nouveaux messages
├── rag.py                # Documents : index des dossiers (SQLite, FTS5, vecteurs Ollama), surveillance, questions, résumés
├── docreader.py          # Documents : texte des fichiers (PDF, Office, OpenDocument, RTF, HTML, e-mails, EPUB…)
├── ollama_admin.py       # Section Ollama : serveur, modèles installés, mises à jour, téléchargements, bibliothèque d'ollama.com
├── files.py              # Fichiers : fiche d'un fichier ou d'un dossier (propriétés de Windows, aperçu, parcours), résumé et questions (Ollama)
├── locks.py              # Fichiers : processus qui utilisent un fichier ou un dossier (handles, DLL), fin des processus, relance en administrateur
├── calc.py               # Calculatrice : historique, sessions, calculs modifiés et recalculés, assistant (Ollama, outils)
├── calc_worker.py        # Calculatrice : interpréteur Python (fonctions prêtes à l'emploi, résultat de la dernière expression)
├── terminals.py          # Consoles : shells ConPTY (pywinpty)
├── notes.py              # Notes
├── captures.py           # Captures : zone de l'écran (Win+Maj+S), onglets, export
├── recorder.py           # Enregistreur : son du PC (+ micro) ou micro seul, WAV, conversion MP3/M4A, texte en direct, fichiers
├── wasapi.py             # Audio Windows (WASAPI) : périphériques, capture loopback et micro, lecture, volume des applications
├── speech.py             # Lecture à voix haute : edge-tts, Piper ou SAPI, phrase par phrase, lecteur
├── ducking.py            # Son des autres applications baissé pendant les annonces (mélangeur de Windows)
├── transcriber.py        # Transcription en texte (Whisper via faster-whisper), file d'attente, en direct
├── webpane.py           # Sites intégrés : contrôle WebView2 posé sur la fenêtre, assistant de réponse
├── whatsapp.py           # WhatsApp Web (lecture de la discussion et de la liste, zone de saisie, nouveaux messages)
├── gmail.py              # Gmail (lecture du fil, éditeur de réponse, résumé de la boîte, non lus, nouveaux e-mails)
├── custom_tasks.py       # Tâches créées depuis l'interface, raccourcis
├── clipboard_listener.py # Presse-papiers Win32 (écoute push, lecture, écriture)
├── winutil.py            # Utilitaires Win32 (premier plan, fenêtre active, icônes, miniatures et captures des fenêtres, explorateur, corbeille, liens, touche Impr. écran, inactivité)
├── engine.py             # Logger (journal), TaskManager, HotkeyManager, instance de lecture (tts)
├── diagnostics.py        # Journal sur disque, exceptions, logging et stdout/stderr capturés, plantages, rapport de diagnostic
├── storage.py            # Persistance (réglages, conversations, consoles, notes, captures)
├── tasks.py              # Tâches planifiées (à personnaliser)
├── bindings.py           # Raccourcis clavier (à personnaliser)
├── web/
│   ├── index.html
│   ├── css/app.css       # Thème et mise en page
│   ├── js/               # main.js (coquille), layout.js (écran partagé), banner.js (bandeau d'Ollama), palette.js (recherche Ctrl+K), reminders.js (menu et fenêtre des rappels), ui.js (composants), composer.js (zone de saisie des sections avec un modèle), pin.js (panneau premier plan), views/ (une vue par section)
│   └── vendor/           # Bibliothèques embarquées (voir vendor/licenses)
├── tests/                # Tests sans interface ni Ollama (python -m tests)
└── requirements.txt
```

Les données sont stockées dans `%LOCALAPPDATA%\Boostache\Boostache` (bouton **Ouvrir** dans les paramètres) ; les sessions WhatsApp Web et Gmail sont dans les sous-dossiers `whatsapp` et `gmail` (les supprimer déconnecte), l'index des documents dans `rag` (le supprimer, Boostache fermé, le fait reconstruire), les journaux dans `logs`. Les enregistrements audio sont à part, dans `Musique\Boostache` par défaut. La variable d'environnement `BOOSTACHE_DATA_DIR` permet d'utiliser un autre dossier, par exemple pour des essais.

---

## Dépendances

| Package | Rôle |
|---|---|
| `pywebview` | Fenêtre native affichant l'interface web (WebView2) |
| `pywinpty` | Pseudo-consoles Windows (ConPTY) pour les terminaux |
| `ollama` | Client API Ollama (chat LLM) |
| `pystray` | Icône system tray |
| `Pillow` | Images (tray, captures, miniatures) |
| `schedule` | Planification des tâches |
| `keyboard` | Hotkeys globaux système |
| `platformdirs` | Chemin de données utilisateur |
| `edge-tts` | Lecture à voix haute avec les voix naturelles de Microsoft (en ligne) |
| `piper-tts` | Lecture à voix haute hors ligne (voix neuronale locale) |
| `comtypes` | API audio de Windows (WASAPI) pour l'enregistreur et la lecture ; voix SAPI |
| `numpy` | Mixage et niveaux de l'enregistreur ; vecteurs des documents |
| `faster-whisper` | Transcription des enregistrements en texte (Whisper, local) |
| `truststore` | Connexions HTTPS (modèles Whisper, voix naturelles) avec les certificats de Windows |
| `pypdf` | Texte des PDF (section Documents) ; `pymupdf`, s'il est installé, est préféré (plus rapide) |
| `openpyxl` | Texte des classeurs Excel (section Documents) |
| `watchdog` | Surveillance des dossiers indexés (section Documents) ; sans lui, nouveau parcours toutes les 30 min |

Embarqués dans `web/vendor/` (aucun accès réseau) : [markdown-it], [highlight.js], [xterm.js], icônes [Lucide], polices Inter et JetBrains Mono.

[pywebview]: https://pywebview.flowrl.com
[WebView2]: https://developer.microsoft.com/microsoft-edge/webview2/
[markdown-it]: https://github.com/markdown-it/markdown-it
[highlight.js]: https://highlightjs.org
[xterm.js]: https://xtermjs.org
[Lucide]: https://lucide.dev
[edge-tts]: https://github.com/rany2/edge-tts
[Piper]: https://github.com/OHF-Voice/piper1-gpl

import mysql.connector
import asyncio
import discord
from discord.ext import tasks, commands
import datetime
import secrets
import numpy as np
from PIL import Image, ImageDraw
import random
import os

client = commands.Bot(command_prefix=".")
LEO = secrets.LEO
BOUNTIES = secrets.BOUNTIES
cnx = mysql.connector.connect(user=secrets.DBUSER, password=secrets.PASSWORD, \
host=secrets.HOST, database=secrets.DBNAME)
cursor = cnx.cursor(buffered=True)
BOUNTY_TIME_DAILY = {"h":10, "m":0} #the UTC time to send the announcement about a bounty every day
LEVEL_XP = 320

#image manipulation
def get_frame_pieces(element_x, element_y): #returns 9 image objects
    with Image.open("frame.png") as im:
        tl = (0, 0, element_x, element_y)
        t = (element_x, 0, element_x*2, element_y)
        tr = (element_x*2, 0, element_x*3, element_y)
        ml = (0, element_y, element_x, element_y*2)
        m = (element_x, element_y, element_x*2, element_y*2)
        mr = (element_x*2, element_y, element_x*3, element_y*2)
        bl = (0, element_y*2, element_x, element_y*3)
        b = (element_x, element_y*2, element_x*2, element_y*3)
        br = (element_x*2, element_y*2, element_x*3, element_y*3)
        boxes = (tl, t, tr, ml, m, mr, bl, b, br)
        arr = [im.crop(box) for box in boxes]
        im.close()
        return arr

def hstitch(im1, im2): #take two pillow images of equal width as input
    h1 = im1.height
    h2 = im2.height
    if h1 != h2:
        return
    w1 = im1.width
    w2 = im2.width
    im = Image.new("RGBA", (w1+w2, h1), "#ffffff")
    im.paste(im1, (0, 0, w1, h1))
    im.paste(im2, (w1, 0, w1+w2, h1))
    return im

def vstitch(im1, im2): #take two pillow images of equal height as input
    w1 = im1.width
    w2 = im2.width
    if w1 != w2:
        return
    h1 = im1.height
    h2 = im2.height
    im = Image.new("RGBA", (w1, h1+h2), "#ffffff")
    im.paste(im1, (0, 0, w1, h1))
    im.paste(im2, (0, h1, w1, h1+h2))
    return im

def build_frame(w, h, element_x, element_y): #accepts two ints. returns im
    tl, t, tr, ml, m, mr, bl, b, br = get_frame_pieces(element_x, element_y)
    corner = {(0, 0):tl, (w-1, 0):tr, (0, h-1):bl, (w-1, h-1):br}
    im = Image.new("RGBA", (w*element_x, h*element_y), "#ffffff")
    for x in range(w):
        for y in range(h):
            if (x == 0 or x == w-1) and (y == 0 or y == h-1):
                im.paste(corner[(x, y)], (x*element_x, y*element_y, (x+1)*element_x, (y+1)*element_y))
            elif x == 0:
                im.paste(ml, (x*element_x, y*element_y, (x+1)*element_x, (y+1)*element_y))
            elif x == w-1:
                im.paste(mr, (x*element_x, y*element_y, (x+1)*element_x, (y+1)*element_y))
            elif y == 0:
                im.paste(t, (x*element_x, y*element_y, (x+1)*element_x, (y+1)*element_y))
            elif y == h-1:
                im.paste(b, (x*element_x, y*element_y, (x+1)*element_x, (y+1)*element_y))
            else:
                im.paste(m, (x*element_x, y*element_y, (x+1)*element_x, (y+1)*element_y))
    return im

#function to give a user some XP
async def add_xp_to_user(ctx, xp):
    user_id = get_player_id(ctx)

    #fetch xp before
    cursor.execute(f"""
    SELECT xp
    FROM users
    WHERE idusers = %s
    """, (user_id,))
    xpi = cursor.fetchone()[0]

    #add to xp
    cursor.execute(f"""
    UPDATE users
    SET xp = xp + %s
    WHERE idusers = %s
    """, (xp, user_id))
    cnx.commit()

    #fetch xp after
    cursor.execute(f"""
    SELECT xp
    FROM users
    WHERE idusers = %s
    """, (user_id,))
    xpf = cursor.fetchone()[0]

    li = xpi // LEVEL_XP
    lf = xpf // LEVEL_XP

    if lf > li:
        if lf - li == 1:
            await ctx.send(f"+{xpf-xpi} XP.\nLEVEL UP: LEVEL {lf}!")
        else:
            await ctx.send(f"+{xpf-xpi} XP.\nLEVEL UP: LEVEL {lf}! Gained {lf - li} level(s).")
    elif lf < li:
        await ctx.send(f"-{xpi-xpf} XP.\nLost {li - lf} levels. You are now Level {lf}.")
    elif xpf > xpi:
        await ctx.send(f"+{xpf-xpi} XP!")
    elif xpi > xpf:
        await ctx.send(f"-{xpi-xpf} XP.")
    return


###achievement set up section
mtr_achievements = None

class AchievementGroup:
    def __init__(self, achievements, title_image, render_frame_w, render_frame_h): # achievements is a list of tuples, or another iterable
        self.achievements = achievements
        self.title = title_image #this is a string which is a filename. The image should be 128 tall, ??? wide
        self.render_frame_w = render_frame_w
        self.render_frame_h = render_frame_h

    async def render_and_save(self, ctx): #return the filename
        frame = build_frame(self.render_frame_w, self.render_frame_h, 128, 128)
        for achievement in self.achievements:
            im, start_coords = await achievement.get_icon_and_position(ctx)
            frame.paste(im, start_coords)
        
        with Image.open(self.title) as title:
            frame = vstitch(title, frame)

        temp = random.randint(0, 1000000)
        frame.save(f"{temp}.png")
        frame.close()
        return f"{temp}.png"




class Achievement:
    def __init__(self, image, name, description, achievement_screen_position, achievement_id, xp):
        self.image = image
        self.name = name
        self.description = description
        self.achievement_screen_position = achievement_screen_position #this will be some pixel co-ordinates. layout plan: 4 across per row, 3 down at least, for now.
        self.achievement_id = achievement_id
        self.xp = xp

    async def congratulate(self, ctx):
        with open(self.image, "rb") as achievement_image:
            f = discord.File(achievement_image, filename=self.image)
        await ctx.send(f"Congratulations! {get_nickname(ctx)} has completed the Achievement: {self.name}.", file=f)
        return

    async def get_icon_and_position(self, ctx): #NOT to be used on basic achievement objects
        print(self.image)
        boole = await self.check_if_completed(ctx)
        if boole:
            with Image.open(self.image) as im:
                im.load()
                return im, self.achievement_screen_position
        else:
            with Image.open("locked.png") as im:
                im.load()
                return im, self.achievement_screen_position


class CompletionAchievement(Achievement):
    def __init__(self, image, name, description, achievement_screen_position, achievement_id, xp, category_id, completion_mode, completion_threshold):
        super().__init__(image, name, description, achievement_screen_position, achievement_id, xp)
        self.category_id = category_id
        self.completion_mode = completion_mode
        self.completion_threshold = completion_threshold

    async def check_if_completed(self, ctx):
        if self.completion_mode == "all":
            #finding the places the player hasn't yet visited
            player_id = get_player_id(ctx)

            cursor.execute(f"""
            SELECT COUNT(DISTINCT idplaces)
            FROM places
            WHERE EXISTS 
            (SELECT * FROM place_categorisation WHERE category_id = %s AND place_categorisation.place_id = places.idplaces)
            AND NOT EXISTS
            (SELECT * FROM visits WHERE visits.visit_place_id = places.idplaces AND user_id = %s)
            """, (self.category_id, player_id))
            places_yet_to_be_visited = cursor.fetchone()[0]

            if places_yet_to_be_visited == 0:
                cursor.execute(f"""
                SELECT *
                FROM achieved_achievements
                WHERE player_id = %s
                AND achievement_id = %s
                """, (player_id, self.achievement_id))
                data = cursor.fetchall()
                if len(data) == 0: #make a record: achievement has been completed
                    cursor.execute(f"""
                    INSERT INTO achieved_achievements (player_id, achievement_id, time_of_achievement)
                    VALUES (%s, %s, %s)
                    """, (player_id, self.achievement_id, datetime.datetime.utcnow()))
                    cnx.commit()
                    await self.congratulate(ctx)
                    await add_xp_to_user(ctx, self.xp)
                return True
            else:
                return False

    async def check_if_still_completed(self, ctx):
        if self.completion_mode == "all":
            #finding the places the player hasn't yet visited
            player_id = get_player_id(ctx)

            cursor.execute(f"""
            SELECT COUNT(DISTINCT idplaces)
            FROM places
            WHERE EXISTS 
            (SELECT * FROM place_categorisation WHERE category_id = %s AND place_categorisation.place_id = places.idplaces)
            AND NOT EXISTS
            (SELECT * FROM visits WHERE visits.visit_place_id = places.idplaces AND user_id = %s)
            """, (self.category_id, player_id))
            places_yet_to_be_visited = cursor.fetchone()[0]

            if places_yet_to_be_visited == 0:
                pass
            else:
                cursor.execute(f"""
                SELECT *
                FROM achieved_achievements
                WHERE player_id = %s
                AND achievement_id = %s
                """, (player_id, self.achievement_id))
                data = cursor.fetchall()

                if len(data) == 1: #is there a record when there shouldn't be?
                    cursor.execute(f"""
                    DELETE 
                    FROM achieved_achievements
                    WHERE player_id = %s
                    AND achievement_id = %s
                    """, (player_id, self.achievement_id))
                    cnx.commit()
                    
                    await ctx.send(f"Due to a visit deletion, the achievement: {self.name} has been revoked.")
                    await add_xp_to_user(ctx, -self.xp)
                return True
        return

async def set_up_achievements(ctx=None):
    
    #setting up the MTR achievements
    cursor.execute(f"""
    SELECT image_file, achievement_name, achievement_description, render_x, render_y, idachievements, achievement_xp, a.category_id
    FROM achievements
    INNER JOIN achievements_aux_1 a ON a.achievement_id = achievements.idachievements
    WHERE render_area_id = 1
    """)
    data = cursor.fetchall()
    achievement_list = []
    for datum in data:
        achievement_list.append(CompletionAchievement(datum[0], datum[1], datum[2], (int(datum[3]), int(datum[4])), int(datum[5]), int(datum[6]), int(datum[7]), "all", 100))

    global mtr_achievements
    mtr_achievements = AchievementGroup(tuple(achievement_list), "mtr_title.png", 7, 6)

    #end
    if ctx:
        await ctx.send("Achievement-related data has been refreshed.")
    else:
        print("Achievements have been loaded.")
    return


#function to check whether a person is of a particular role
def allowed(ctx, *roles):
    haves = [role.name for role in ctx.author.roles]
    for have in haves:
        if have in roles:
            return True
        else:
            pass
    return False 


#function to get someone's user ID
def get_player_id(ctx):
    
    cursor.execute(f"""
    SELECT idusers
    FROM users 
    WHERE discord_id = %s""",
    (ctx.author.id,))
    
    return int(cursor.fetchone()[0])

def get_nickname(ctx):
    cursor.execute(f"""
    SELECT nickname
    FROM users
    WHERE discord_id = %s""",
    (ctx.author.id,))

    return cursor.fetchone()[0]

#function to add a new player to the database

def add_new_user(ctx):  
    cursor.execute(f"""
    INSERT INTO users (discord_id) 
    VALUES (%s)""",
    (ctx.author.id,))    
    cnx.commit()
    return cursor.lastrowid

def create_table(data, title, column_headings): #the command will take the list of tuples from mysql, and transform it into well-presented data.
    #data should be a list of tuples, only passed through from the cursor.fetchall command
    #the function will return a list of strings that have a length under 1600 characters
    #checking for empty data
    if len(data) == 0:
        return ["No Data."]

    data.insert(0, column_headings)
    string = title + "\n"
    stringlist = []
    #identifying the number of rows and columns that need to be dealt with
    columns = len(data[0])

    #finding the longest spacing of each column
    column_widths = [0]*columns

    #data processing - pass 1
    for row in data:
        for i in range(columns):
            if len(str(row[i])) > column_widths[i]:
                column_widths[i] = len(str(row[i]))

    #data formatting - pass 2.
    #put it in the form
    # ```
    # XXXX | XX | XXXXX
    # XXXX | XX | XXXXX
    # ```
    # and ensure if it's over 1600 char that it gets cut off
    for i in range(len(data)):
        for j in range(columns):
            string = string + str(data[i][j]) + (" "*(column_widths[j] - len(str(data[i][j])))) + "|"
        
        string = string[:-1] + "\n"
        if len(string) > 1600:
            string = "```" + string + "```"
            stringlist.append(string)
            string = ""
    #now the width of each column is present in column widths
    #now justified columns
    #now got string back in 1600-2000 char chunks, will return it
    string = "```" + string + "```"
    stringlist.append(string)
    return stringlist

async def search_for_place(ctx, place): #returns place id, place name
    cursor.execute(f"""
    SELECT idplaces, place_name, place_xp
    FROM places
    WHERE place_name LIKE %s
    """, (place + "%",))
    data = cursor.fetchall()
    if len(data) > 1:
        await ctx.send(f"There are multiple places with names starting with {place}. See below for details, then try the command again.")
        table = create_table(data, f"--- SIMILAR PLACES TO {place}", ("Place ID", "Place Name"))
        for message in table:
            await ctx.send(message)
        return
    elif len(data) == 1:
        id = data[0][0]
        place_name = data[0][1]
        xp = data[0][2]
        return (id, place_name, xp)
    else:
        await ctx.send(f"There are no places with names that start with \"{place}\"." \
        "Please send the command again, or use .browse_places <seach term> to see the places in the database.")
        return

async def search_for_category(ctx, category): #returns category id, category name
    cursor.execute(f"""
    SELECT idplace_categories, category_name
    FROM place_categories
    WHERE category_name LIKE %s
    """, (category + "%",))
    data = cursor.fetchall()
    if len(data) > 1:
        await ctx.send(f"There are multiple categories with names starting with {category}. See below for details, then try the command again.")
        table = create_table(data, f"--- SIMILAR PLACES TO {category}", ("Category ID", "Category Name"))
        for message in table:
            await ctx.send(message)
        return False
    elif len(data) == 1:
        id = data[0][0]
        category_name = data[0][1]
        return (id, category_name)
    else:
        await ctx.send(f"There are no categories with names that start with \"{category}\"." \
        "Please send the command again, or use .browse_categories <seach term> to see the places in the database.")
        return False


@client.event
async def on_ready():
    global BOUNTIES
    print('We have logged in as {0.user}'.format(client))
    await set_up_achievements()
    BOUNTIES = await client.fetch_channel(BOUNTIES)
    return

@client.command(brief="Leo uses this to add another bounty if the day's bounty is shit.")
async def create_tomorrows_bounty(ctx):
    global LEO
    if ctx.author.id != LEO:
        return
        
    dat = datetime.datetime.utcnow() + datetime.timedelta(hours=2)
    dat = datetime.datetime(year=dat.year, month=dat.month, day=dat.day + 1, hour=0, minute=0, second=0)
    await set_up_bounty(dat)
    return

@client.command(brief="Leo uses this to create today's bounty.")
async def create_todays_bounty(ctx):
    global LEO
    if ctx.author.id != LEO:
        return
        
    dat = datetime.datetime.utcnow() + datetime.timedelta(hours=2)
    dat = datetime.datetime(year=dat.year, month=dat.month, day=dat.day, hour=0, minute=0, second=0)
    await set_up_bounty(dat)
    return    


##Bounty System


async def set_up_bounty(dat):
    global BOUNTIES
    #select a random place
    cursor.execute(f"""
    SELECT idplaces, place_name, place_xp
    FROM places
    WHERE place_xp > 101
    ORDER BY RAND()
    LIMIT 1
    """)
    (place_id, place_name, xp) = cursor.fetchone()

    #generate an xp amount
    z = np.random.normal(1) #standard normal randomly distributed variable
    xp = int((xp/100)**1.2 * 500 * (1 + z/20))
    #generate the time the bounty finishes at
    start_time = dat
    finish_time = dat + datetime.timedelta(hours=24)

    #send the message about the bounties
    message = await BOUNTIES.send(f"**NEW BOUNTY!**\nPlace: {place_name}\nOpens:{start_time + datetime.timedelta(hours=8)}\nCloses: {finish_time + datetime.timedelta(hours=8)} HK Time\nXP: {xp}")
    #add the bounty to the database
    cursor.execute(f"""
    INSERT INTO bounties (xp, place_id, start_time, end_time, message_id)
    VALUES (%s, %s, %s, %s, %s)        
    """, (xp, place_id, start_time, finish_time, message.id))
    cnx.commit()

    #delete any bounty messages for bounties which have expired
    #finding IDs
    cursor.execute(f"""
    SELECT idbounties, message_id
    FROM bounties
    WHERE end_time < %s
    AND message_id != 0
    """, (datetime.datetime.utcnow(),))
    data = cursor.fetchall()
    if len(data) > 0:
        for row in data:           
            try:
                await BOUNTIES.delete_messages((await BOUNTIES.fetch_message(row[1]),))
            except:
                print("Tried to delete a bounty-related discord message, but it had already been deleted.")
            cursor.execute(f"""
            UPDATE bounties
            SET message_id = 0
            WHERE idbounties = %s
            """, (row[0],))
            cnx.commit()

    return


async def check_if_bounties_completed(ctx):
    #get the current time
    dat = datetime.datetime.utcnow()
    dat14 = dat - datetime.timedelta(days=14)

    #request a list of all bounties which closed in the last 14 days
    cursor.execute(f"""
    SELECT idbounties, place_id, start_time, end_time, xp
    FROM bounties
    WHERE end_time > %s    
    """, (dat14,))
    data = cursor.fetchall()
    uid = get_player_id(ctx)
    #for each bounty,
    for row in data:
        #check whether their bounty completion is in the database
        
        cursor.execute(f"""
        SELECT idbeaten_bounties
        FROM beaten_bounties
        WHERE player_id = %s
        AND bounty_id = %s
        """, (uid, row[0]))
        bounty_data = cursor.fetchall()
        completion_is_recorded = (not (len(bounty_data) == 0))
        #check whether there exists a visit to the place in the time interval by the user
        cursor.execute(f"""
        SELECT p.place_name
        FROM visits v
        INNER JOIN places p ON p.idplaces = v.visit_place_id
        WHERE visit_place_id = %s
        AND user_id = %s
        AND visit_time BETWEEN %s AND %s
        """, (row[1], uid, row[2], row[3]))
        visit_data = cursor.fetchall()
        #if bounty not registered, add their bounty completion to the database, and reward them with the corresponding amount of xp, otherwise ignore
        if len(visit_data) > 0 and not completion_is_recorded:
            cursor.execute(f"""
            INSERT INTO beaten_bounties (player_id, bounty_id)
            VALUES (%s, %s)
            """, (uid, row[0]))
            cnx.commit()
            await ctx.send(f"BOUNTY COMPLETE: Visit {visit_data[0][0]}")
            await add_xp_to_user(ctx, int(row[4]))  
        #if there is no visit satisfying the bounty, but the completion is also in the database    
        elif len(visit_data) == 0 and completion_is_recorded:
            #cancel the bounty, and remove the corresponding amount of xp 
            cursor.execute(f"""
            DELETE FROM beaten_bounties
            WHERE idbeaten_bounties = %s
            """, (bounty_data[0][0],))
            cnx.commit()
            await ctx.send(f"Bounty Completion Revoked.")
            await add_xp_to_user(ctx, -int(row[4]))
    return
       
                 


#this allows a user to enter themselves into the users table of the database
@client.command(brief="Use this command once to register yourself with the bot.")
async def register(ctx):
    try:    
        await ctx.send(f"Added new user: ID number {add_new_user(ctx)}")
    except:
        pass
    await ctx.author.avatar_url_as(format="png", size=128).save(f"{ctx.author.id}.png")
    return


#this allows a user to set their own nickname
@client.command(brief="Use this command to set your nickname.")
async def nickname(ctx, nick):
    cursor.execute(f"""
    UPDATE users
    SET nickname = %s
    WHERE discord_id = %s
    """, (nick, ctx.author.id))
    cnx.commit()
    await ctx.send(f"Your nickname has been successfully changed to {nick}.")
    return


#this is essentially a dump of the users table inside the database
@client.command(brief="Use this command to show the XP leaderboard of users.")
async def leaderboard(ctx):
    cursor.execute(f"""
    SELECT nickname, xp DIV {LEVEL_XP}, CONCAT(xp % {LEVEL_XP}, '/{LEVEL_XP}')
    FROM users
    ORDER BY xp DESC
    """)
    data = cursor.fetchall()
    title = "--- ROSTER OF USERS ---"
    column_headings = ("Nickname", "Level", "XP")
    table = create_table(data, title, column_headings)
    for message in table:
        await ctx.send(message)
    return



#the most important command to the end-user, which lets them log a visit to a place, which is automatically considered to be at the time of the bot processing it
@client.command(brief="Use this command to register your visit to a location.")
async def visit(ctx, *place, **kwargs):
    try:
        time = kwargs["time"]
        place = kwargs["place"]
    except:
        time = datetime.datetime.utcnow()
    place = " ".join(place)
    #making sure there is only one unique place
    id, place_name, xp = await search_for_place(ctx, place)

    #seeing if the person has visited the place before
    cursor.execute(f"""
    SELECT * 
    FROM visits
    WHERE user_id = %s
    AND visit_place_id = %s
    """, (get_player_id(ctx), id))
    data = cursor.fetchone()

    #actually inputting the data into the database
    cursor.execute(f"""
    INSERT INTO visits (user_id, visit_time, visit_place_id)
    VALUES (
        (SELECT idusers FROM users WHERE discord_id = %s),
        %s,
        %s
    )
    """, (ctx.author.id, time, id))
    cnx.commit()
    await ctx.send(f"Visit to {place_name} recorded by {get_nickname(ctx)} at {time}. Visit ID: {cursor.lastrowid}")

    if data == None:
        await add_xp_to_user(ctx, xp)


    #check achievement progress
    for achievement in mtr_achievements.achievements:
        await achievement.check_if_completed(ctx)

    await check_if_bounties_completed(ctx)

    return


#this command allows people to record visits that happened in the past.
#if people want to manually change the time of their visits, they can just tell me, I don't imagine there'll be too much need for that.
@client.command(brief="Use this to record a visit that took place yesterday or before.")
async def pastvisit(ctx, year, month, date, *place):
    try:
        time = datetime.datetime(year=int(year), month=int(month), day=int(date))
    except:
        await ctx.send("There was something wrong in your formatting of the date. Type \".pastvisit YYYY MM DD place\".")
        return
    await visit(ctx, place=place, time=time)
    return


@client.command(brief="Use this to delete a visit.")
async def deletevisit(ctx, visit_id):
    try:
        visit_id = int(visit_id)
    except:
        await ctx.send("The visit ID should be an integer.")
    
 
    cursor.execute(f"""
    SELECT user_id, visit_place_id FROM visits
    WHERE idvisits = %s
    """, (visit_id,))
    try:
        (user_id, place_id) = cursor.fetchone()
    except:
        pass

    if user_id != get_player_id(ctx):
        await ctx.send("This is not your visit to delete.")
        return

    cursor.execute(f"""
    SELECT place_xp
    FROM places
    WHERE idplaces = (SELECT visit_place_id FROM visits WHERE idvisits = %s)
    """, (visit_id,))
    xp = int(cursor.fetchone()[0])
    
    cursor.execute(f"""
    DELETE
    FROM visits
    WHERE idvisits = %s
    """, (visit_id,))
    cnx.commit()
    await ctx.send(f"Visit with ID {visit_id} deleted.")

    #check if the person has never visited before, and if so, revoke their xp
    cursor.execute(f"""
    SELECT *
    FROM visits
    WHERE visit_place_id = %s
    AND user_id = %s
    """, (place_id, user_id))
    data = cursor.fetchall()
    if len(data) == 0:
        await add_xp_to_user(ctx, -xp)

    for achievement in mtr_achievements.achievements:
        await achievement.check_if_still_completed(ctx)
    
    await check_if_bounties_completed(ctx)

    return

@client.command(brief="Use this to see your recent visits. (Default: 5 visits.)")
async def showmyvisits(ctx, number_of_visits=5):
    try:
        number_of_visits = int(number_of_visits)
    except:
        await ctx.send("number_of_visits should be an integer.")

    user_id = get_player_id(ctx)

    cursor.execute(f"""
    SELECT visits.idvisits, visits.visit_time, places.place_name 
    FROM visits 
    JOIN places ON places.idplaces=visits.visit_place_id
    WHERE user_id = %s
    ORDER BY visits.visit_time DESC
    LIMIT %s
    """, (user_id, number_of_visits))

    data = cursor.fetchall()

    messages = create_table(data, f"Your Most Recent {number_of_visits} Visits.", ("Visit ID", "Visit Time", "Place Name"))
    for message in messages:
        await ctx.send(message)
    return 


@client.command(brief="Updaters use this to add new place categories.")
async def addcat(ctx, *category_name):
    if not allowed(ctx, "Updater"):
        ctx.send("Unauthorised.")
        return
    category_name = " ".join(category_name)
    cursor.execute(f""" 
    INSERT INTO place_categories (category_name)
    VALUES (%s)
    """, (category_name,))
    cnx.commit()
    await ctx.send(f"Category {category_name} successfully added.")
    return

@client.command(brief="Updaters use this to add places to categories.")
async def addtocat(ctx, *place_name_into_category_name):
    #collecting data
    if not allowed(ctx, "Updater"):
        ctx.send("Unauthorised.")
        return
    split = place_name_into_category_name.index("into")
    place_name = " ".join(place_name_into_category_name[:split])
    category_name = " ".join(place_name_into_category_name[split+1:])
    try:
        place_id, place_name, xp = await search_for_place(ctx, place_name)
        xp = None
        category_id, category_name = await search_for_category(ctx, category_name)
    except:
        await ctx.send("Some error occured, see the other messages for details.")
        return
    #inputting into the database
    cursor.execute(f""" 
    INSERT INTO place_categorisation (place_id, category_id)
    VALUES (%s, %s)
    """, (place_id, category_id))
    cnx.commit()
    await ctx.send(f"Place {place_name} has been inserted into category {category_name}.")
    return

@client.command(brief="Updaters use this to rename categories.")
async def rename_category(ctx, *cat_one_into_cat_two):
    if not allowed(ctx, "Updater"):
        ctx.send("Unauthorised.")
        return
    #collecting data
    split = cat_one_into_cat_two.index("into")
    category_old_name = " ".join(cat_one_into_cat_two[:split])
    category_new_name = " ".join(cat_one_into_cat_two[split+1:])
    try:
        category_id, category_old_name = await search_for_category(ctx, category_old_name)
    except:
        await ctx.send("Some error occured, see the other messages for details.")
        return
    #inputting into the database
    cursor.execute(f""" 
    UPDATE place_categories
    SET category_name = %s
    WHERE idplace_categories = %s
    """, (category_new_name, category_id))
    cnx.commit()
    await ctx.send(f"Category ({category_old_name}) has been renamed ({category_new_name}).")
    return

@client.command(brief="Use this to browse place categories.")
async def browse_categories(ctx, *category):
    if not category:
        category = [""]
    cursor.execute(f"""
    SELECT idplace_categories, category_name
    FROM place_categories
    WHERE category_name LIKE %s
    """, (" ".join(category) + "%",))
    data = cursor.fetchall()
    table = create_table(data, "--- SEARCH RESULTS ---", ("Category ID", "Category Name"))
    for message in table:
        await ctx.send(message)
    return

@client.command(brief="Use this to see all the places in a category.")
async def show_category(ctx, *category):
    category_id, category_name = await search_for_category(ctx, " ".join(category))

    #finding the places in the category in total
    cursor.execute(f""" 
    SELECT DISTINCT idplaces, place_name, place_xp
    FROM places
    WHERE EXISTS
    (SELECT * FROM place_categorisation WHERE category_id = %s AND place_categorisation.place_id = places.idplaces)
    """, (category_id,))
    all_places = cursor.fetchall()
    for message in create_table(all_places, f"Places in the Category: {category_name}", ("Place ID", "Place Name", "XP Reward")):
        await ctx.send(message)
    return

@client.command(brief="Use this to check your xp and level.")
async def level(ctx):
    filename = await render_profile(ctx)
    with open(filename, "rb") as achievement_image:
        f = discord.File(achievement_image, filename="rendered_profile.png")
        await ctx.send(file=f)
    os.remove(filename)
    pass

async def render_profile(ctx): #need to add a command to register the profile picture of each user upon .register
    global LEVEL_XP
    #get big frame
    frame = build_frame(4, 4, 128, 128)

    #get user's xp
    uid = get_player_id(ctx)
    cursor.execute(f"""
    SELECT xp
    FROM users
    WHERE idusers = %s
    """, (uid,))
    xp = int(cursor.fetchall()[0][0])
    #add corresponding border

    level = xp // LEVEL_XP
    tenlevel = (level // 10) * 10
    border_filename = f"level-{tenlevel}.png"
    with Image.open(border_filename) as border_image:
        frame.alpha_composite(border_image, (128, 64))

    #add profile picture
    did = ctx.author.id
    with Image.open(f"{did}.png") as pfp:
        # pfp = pfp.resize(128, 128)
        frame.alpha_composite(pfp, (192, 128))
    
    #add xp bar
    with Image.open("xp-bar.png") as xpbar:
        frame.alpha_composite(xpbar, (92, 332))
    
    #draw rectangle to symbolise xp
    xpbar_fill_width = int((xp % LEVEL_XP) / LEVEL_XP * 320)
    drawer = ImageDraw.Draw(frame, "RGBA")
    try:
        drawer.rectangle((96, 336, 96 + xpbar_fill_width, 368), fill="#ffffff")
    except:
        print("0 xp, not drawing.")
    #grab images of numerals for the number
    level_numbers = grab_numeral_images(level)

    #draw them onto the image
    digit_count = len(level_numbers)
    for x in range(digit_count):
        xcoord = 256 - (32 * digit_count) + (x * 64)
        frame.alpha_composite(level_numbers[x], (xcoord, 384))
    

    #save the image with a randomly generated filename
    temp = random.randint(0, 1000000)
    frame.save(f"{temp}.png")
    frame.close()
    return f"{temp}.png"

def grab_numeral_images(integer): #takes int, returns list[Images]
    digits = [int(x) for x in str(integer)]
    with Image.open("numerals.png") as numeral_sheet:
        numerals = [numeral_sheet.crop((64*i, 0, 64*(i+1), 64)) for i in range(0, 10)]
    digits = [numerals[x] for x in digits]
    return digits

@client.command(brief="Use this to check your achievements.")
async def achievements(ctx):
    filename = await mtr_achievements.render_and_save(ctx)
    with open(filename, "rb") as achievement_image:
        f = discord.File(achievement_image, filename="rendered_achievements_screen.png")
        await ctx.send(f"Here are your achievements:", file=f)
    os.remove(filename)
    return


@client.command(brief="Use this to show your progress through a category.")
async def progress(ctx, *category):
    catname = " ".join(category)
    try:
        category_id = int(catname)
        cursor.execute(f"""
        SELECT category_name
        FROM place_categories
        WHERE idplace_categories = %s
        """, (category_id,))
        category_name = cursor.fetchone()[0]
    except:
        category_id, category_name = await search_for_category(ctx, catname)

    #finding the places in the category in total
    cursor.execute(f""" 
    SELECT DISTINCT idplaces, place_name, place_xp
    FROM places
    WHERE EXISTS
    (SELECT * FROM place_categorisation WHERE category_id = %s AND place_categorisation.place_id = places.idplaces)
    """, (category_id,))
    all_places = cursor.fetchall()
    total_number_of_places = len(all_places)

    #finding the places the player hasn't yet visited
    cursor.execute(f"""
    SELECT DISTINCT idplaces, place_name, place_xp
    FROM places
    WHERE EXISTS 
    (SELECT * FROM place_categorisation WHERE category_id = %s AND place_categorisation.place_id = places.idplaces)
    AND NOT EXISTS
    (SELECT * FROM visits WHERE visits.visit_place_id = places.idplaces AND user_id = %s)
    """, (category_id, get_player_id(ctx)))
    places_yet_to_be_visited = cursor.fetchall()
    number_of_places_yet_to_be_visited = len(places_yet_to_be_visited)

    #what if the player has visited all the places?!
    if number_of_places_yet_to_be_visited == 0:
        await ctx.send(f"""You have visited all {total_number_of_places} of the places in the {category_name} category! Congratulations!""")
        return

    #outputting some interesting data for the user to see:
    await ctx.send(
    f"""There are {total_number_of_places} places in the category named {category_name}.\n"""
    + f"""You have {number_of_places_yet_to_be_visited} of them to go!\n"""
    + f"""Total progress: {100 - round(number_of_places_yet_to_be_visited*100/total_number_of_places)}% complete.""")

    messages = create_table(places_yet_to_be_visited, f"{category_name} places you haven't yet been to:", ("Place ID", "Place Name", "XP"))
    for message in messages:
        await ctx.send(message)

    return

@client.command(brief="Use this to browse places.")
async def browse_places(ctx, *place):
    if not place:
        place = [""]
    cursor.execute(f"""
    SELECT idplaces, place_name, place_xp
    FROM places
    WHERE place_name LIKE %s
    """, (" ".join(place) + "%",))
    data = cursor.fetchall()
    table = create_table(data, "--- SEARCH RESULTS ---", ("Place ID", "Place Name", "XP"))
    for message in table:
        await ctx.send(message)
    return

@client.command(brief="Updaters use this to add new places.")
async def add(ctx, place):
    if not allowed(ctx, "Updater"):
        ctx.send("Unauthorised.")
        return

    if place == "":
        ctx.send("Empty. Try again.")
    try:
        cursor.execute(f"""
        INSERT INTO places (place_name)
        VALUES (%s)
        """, (place,))
        cnx.commit()
        await ctx.send(f"Place {place} added, with ID {cursor.lastrowid}.")
        return
    except:
        await ctx.send("Database didn't like it, probably that's a repeat name.")
        return

@client.command(brief="Updaters use this to rename places.")
async def rename(ctx, place, new_name):
    if not allowed(ctx, "Updater"):
        ctx.send("Unauthorised.")
        return

    if place == "" or new_name == "":
        ctx.send("Empty. Try again.")
    
    try:
        cursor.execute(f"""
        UPDATE places
        SET place_name = %s
        WHERE place_name = %s
        """, (new_name, place))
        cnx.commit()
        await ctx.send(f"{place} renamed to {new_name}.")
        return
    except:
        await ctx.send("Database didn't like it, that's probably a repeat name.")
        return

@client.command(brief="One Command Fixes Approximately All.")
async def test(ctx):
    await ctx.send("Discord bot is active. The bot will ping the database. If the database connection had been dropped, the bot will reconnect.")
    cnx.ping(reconnect=True)
    await set_up_achievements(ctx)
    return

client.run(secrets.CLIENT)
cnx.close()
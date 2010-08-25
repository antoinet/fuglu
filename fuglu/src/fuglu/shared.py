#   Copyright 2009 Oli Schacher
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# $Id$
#
import logging
import os
import time
import socket

_HASHLIB=0
_MD5=1

MD5LIB=-1
try:
    import hashlib
    MD5LIB=_HASHLIB
except:
    MD5LIB=_MD5
    import md5
    
import random
import email
import re
import unittest
import ConfigParser
import datetime
from string import Template
#constants

DUNNO=0 #go on
ACCEPT=1 # accept message, no further tests
DELETE=2 # blackhole, no further tests
REJECT=3 # reject, no further tests
DEFER=4 # defer, no further tests

ALLCODES={
          'DUNNO':DUNNO,
          'ACCEPT':ACCEPT,
          'DELETE':DELETE,
          'REJECT':REJECT,
          'DEFER':DEFER,
          }

def actioncode_to_string(actioncode):
    """Return the human readable string for this code"""
    for key,val in ALLCODES.items():
        if val==actioncode:
            return key
    return 'INVALID ACTION CODE %s'%actioncode

def string_to_actioncode(actionstring,config=None):
    """return the code for this action"""
    upper=actionstring.upper().strip()
    if config!=None:
        if upper=='DEFAULTHIGHSPAMACTION':
            confval=config.get('spam','defaulthighspamaction').upper()
            if not ALLCODES.has_key(confval):
                return None
            return ALLCODES[confval]
       
        if upper=='DEFAULTLOWSPAMACTION':
            confval=config.get('spam','defaultlowspamaction').upper()
            if not ALLCODES.has_key(confval):
                return None
            return ALLCODES[confval]
       
        if upper=='DEFAULTVIRUSACTION':
            confval=config.get('virus','defaultvirusaction').upper()
            if not ALLCODES.has_key(confval):
                return None
            return ALLCODES[confval]
           
    if not ALLCODES.has_key(upper):
        return None
    return ALLCODES[upper]


def apply_template(templatecontent,suspect,values=None):
    if values==None:
        values={}

    values['from_address']=suspect.from_address
    values['to_address']=suspect.to_address
    values['from_domain']=suspect.from_domain
    values['to_domain']=suspect.to_domain
    values['subject']=suspect.getMessageRep()['subject']
    
    template = Template(templatecontent)
    
    message= template.safe_substitute(values)
    return message


HOSTNAME=socket.gethostname()

class Suspect:
    """
    The suspect represents the message to be scanned. Each scannerplugin will be presented
    with a suspect and may modify the tags or even the message content itself.
    """
    
    def __init__(self,from_address,to_address,tempfile):
        self.msgrep=None
        
        #tags set by plugins
        self.tags={}
        self.tags['virus']={}
        self.tags['spam']={}
        self.tags['highspam']={}
        self.tags['decisions']=[]
        
        
        #temporary file containing the message source
        self.tempfile=tempfile
 
        #stuff set from smtp transaction
        self.size=os.path.getsize(tempfile)
        self.from_address=from_address
        self.to_address=to_address  # for plugins supporting only one recipient
        self.recipients=[] # for plugins supporting multiple recipients
        
        #additional basic information
        self.timestamp=time.time()
        self.id=self._generate_id()
        
        #headers 
        self.addheaders={}
        self.replaceheaders={}
        
        #helper attributes
        if self.from_address==None:
            self.from_address=''
            
        try:
            (user, self.to_domain) = self.to_address.rsplit('@',1)
        except:
            raise ValueError,"invalid to email address: %s"%self.to_address
      
        
        if self.from_address=='':
            self.from_domain=''
        else:
            try:
                (user, self.from_domain) = self.from_address.rsplit('@',1)
            except Exception, e:
                raise ValueError,"invalid from email address: '%s'"%self.from_address
    
    def _generate_id(self):
        """
        generate a new id for a message. 
        uses hash of hostname+current time+random int which 
        should be sufficiently unique for the quarantine
        """
        uni="%s%s%s"%(HOSTNAME,time.time(),random.randint(1,10000))
        id=None
        if MD5LIB==_HASHLIB:
            id=hashlib.md5(uni).hexdigest()
        else:
            id=md5.new(uni).hexdigest()
        return id
    
    
    def debug(self,message):
        """Add a line to the debug log if debugging is enabled for this message"""
        if not self.get_tag('debug'):
            return
        isotime=datetime.datetime.now().isoformat()
        fp=self.get_tag('debugfile')
        try:
            fp.write('%s %s\n'%(isotime,message))
            fp.flush()
        except Exception,e:
            logging.getLogger('suspect').error('Could not write to logfile: %s'%e)
            
        
          
    def get_tag(self,key):
        """returns the tag value"""
        if not self.tags.has_key(key):
            return None
        return self.tags[key]
    
    def set_tag(self,key,value):
        """Set a new tag"""
        self.tags[key]=value
        
    def is_highspam(self):
        """Returns True if ANY of the spam engines tagged this suspect as spam"""
        for key in self.tags['highspam'].keys():
            val=self.tags['highspam'][key]
            if val:
                return True
        return False
    
    def is_spam(self):
        """Returns True if ANY of the spam engines tagged this suspect as spam"""
        for key in self.tags['spam'].keys():
            val=self.tags['spam'][key]
            if val:
                return True
        return False
    
    def addheader(self,key,value):
        """adds a entry to the list of headers to be added when re-injecting"""
        self.addheaders[key]=value
        
    
    def is_virus(self):
        """Returns True if ANY of the antivirus engines tagged this suspect as infected"""
        for key in self.tags['virus'].keys():
            val=self.tags['virus'][key]
            if val:
                return True
        return False
    
    def __str__(self):
        """representation good for logging"""
        virusstring="no"
        if self.is_virus():
            virusstring="yes"
        spamstring="no"
        if self.is_spam():
            spamstring="yes"
        
        #fix tag represenations
        tagscopy=self.tags.copy()
        if tagscopy.has_key('SAPlugin.spamscore'):
            tagscopy['SAPlugin.spamscore']="%.2f"%tagscopy['SAPlugin.spamscore']
        
        #remove bloat
        if tagscopy.has_key('decisions'):
            del tagscopy['decisions']
        
        astring="Suspect %s: from=%s to=%s size=%s , spam=%s, virus=%s tags=%s"%(self.id,self.from_address, self.to_address,self.size,spamstring,virusstring,tagscopy)
        return astring
    
    def getMessageRep(self):
        """returns the python email api representation of this suspect"""
        if self.msgrep!=None:
            return self.msgrep
        fh=open(self.tempfile,'r')
        self.msgrep=email.message_from_file(fh)
        fh.close()
        return self.msgrep
    
    def setMessageRep(self,msgrep):
        """replace the message content. this must be a standard python email representation"""
        self.msgrep=msgrep
    
    def getOriginalSource(self):
        try:
            source=open(self.tempfile).read()
        except Exception,e:
            logging.getLogger('fuglu.suspect').error('Cannot retrieve original source from tempfile %s : %s'%(self.tempfile,str(e)))
            raise e
        return source    
        
##it is important that this class explicitly extends from object, or __subclasses__() will not work!
class BasicPlugin(object):
    """Base class for all plugins"""
    
    def __init__(self,config,section=None):
        if section==None:
            self.section=self.__class__.__name__
        else:
            self.section=section
            
        self.config=config
        self.requiredvars=()
    
    def _logger(self):
        """returns the logger for this plugin"""
        myclass=self.__class__.__name__
        loggername="fuglu.plugin.%s"%(myclass)
        return logging.getLogger(loggername)
    
    def lint(self):
        return self.checkConfig()
    
    def checkConfig(self):
        allOK=True
        for configvar in self.requiredvars:
            (section,config)=configvar
            try:
                var=self.config.get(section,config)
            except ConfigParser.NoOptionError:
                print "Missing configuration value [%s] :: %s"%(section,config)
                allOK=False
            except ConfigParser.NoSectionError:
                print "Missing configuration section %s"%(section)
                allOK=False
        return allOK

    def __str__(self):
        classname=self.__class__.__name__
        if self.section==classname:
            return classname;
        else:
            return '%s(%s)'%(classname,self.section)

class ScannerPlugin(BasicPlugin):
    """Scanner Plugin Base Class"""
    def examine(self,suspect):
        self._logger().warning('Unimplemented examine() method')

class PrependerPlugin(BasicPlugin):
    """Prepender Plugins - Plugins run before the scanners that can influence
    the list of scanners being run for a certain message"""
    
    def pluginlist(self,suspect,pluginlist):
        """return the modified pluginlist or None for no change"""
        self._logger().warning('Unimplemented pluginlist() method')
        return None
        
class AppenderPlugin(BasicPlugin):
    """Appender Plugins are run after the scan process (and after the re-injection if the message
    was accepted)"""
    def process(self,suspect,decision):
        self._logger().warning('Unimplemented examine() method')


class HeaderFilter(object):
    """Allows filtering Suspect based on header/tag regexes"""
    def __init__(self,filename):
        self.filename=filename
        self.patterns=[]
        
        self.reloadinterval=30
        self.lastreload=0
        self.logger=logging.getLogger('fuglu.headerfilter')
        self._reloadifnecessary()
        
        
    def _reloadifnecessary(self):
        now=time.time()
        #check if reloadinterval has passed
        if now-self.lastreload<self.reloadinterval:
            return
        if self.filechanged():
            self._reload()
        
    def _reload(self):
        self.logger.info('Reloading Rulefile %s'%self.filename)
        statinfo=os.stat(self.filename)
        ctime=statinfo.st_ctime
        self.lastreload=ctime
        fp=open(self.filename,'r')
        lines=fp.readlines()
        fp.close()
        newpatterns=[]
        
        for line in lines:
            line=line.strip()
            if line=="":
                continue
            if line.startswith('#'):
                continue
            
            #line shold be "headername    regex    arguments"
            sp=line.split(None,2)
            if len(sp)<2:
                self.logger.debug('Ignoring line %s'%line)
            
            args=None
            if len(sp)==3:
                args=sp[2]
                
            headername=sp[0]
            
            regex=sp[1]
            try:
                pattern=re.compile(regex, re.IGNORECASE)
            except Exception,e:
                self.logger.error('Could not compile regex %s in file %s (%s)'%(regex,self.filename,e))
                continue
            
            tup=(headername,pattern,args)
            newpatterns.append(tup)
        self.patterns=newpatterns
    
    
    def _getHeader(self,suspect,headername):
        """return mail header value or special value"""
        if headername=='envelope_from':
            return [suspect.from_address,]
        if headername=='envelope_to':
            return [suspect.to_address,]
        if headername=='from_domain':
            return [suspect.from_domain,]
        if headername=='to_domain':
            return [suspect.to_domain,]
        
        #if it starts with a @ we return a tag, not a header
        if headername[0:1]=='@':
            tagname=headername[1:]
            return [suspect.get_tag(tagname),]
        
        return suspect.getMessageRep().get_all(headername)
        
           
    def matches(self,suspect):
        """returns (True,arg) if any regex matches, (False,None) otherwise"""
        self._reloadifnecessary()
        for tup in self.patterns:
            (headername,pattern,arg)=tup
            vals=self._getHeader(suspect,headername)
            if vals==None:
                self.logger.debug('No header %s found'%headername)
                continue
            
            for val in vals:
                if val==None:
                    continue
                self.logger.debug("""Checking headername %s (arg '%s') against value %s"""%(headername,arg,val))
                if pattern.match(str(val)):
                    self.logger.debug('Match headername %s on val %s'%(headername,val))
                    return (True,arg)
        self.logger.debug('No match found')
        return (False,None)
    
    def getArgs(self,suspect):
        """returns all args of matched regexes in a list"""
        ret=[]
        self._reloadifnecessary()
        for tup in self.patterns:
            (headername,pattern,arg)=tup
            vals=self._getHeader(suspect,headername)
            if vals==None:
                self.logger.debug('No header %s found'%headername)
                continue
            for val in vals:
                self.logger.debug("""Checking headername %s (arg '%s') against value %s"""%(headername,arg,val))
                if val==None:
                    continue
                if pattern.match(str(val)):
                    ret.append (arg)
        return ret
       
    def filechanged(self):
        statinfo=os.stat(self.filename)
        ctime=statinfo.st_ctime
        if ctime>self.lastreload:
            return True
        return False
    
    
  
class HeaderFilterTestCase(unittest.TestCase):
    """Test Header Filter"""
    def setUp(self):     
        self.candidate=HeaderFilter('testdata/headertest.regex')
 
    def tearDown(self):
        pass     

    def test_hf(self):
        """Test header filters"""

        suspect=Suspect('sender@unittests.fuglu.org','recipient@unittests.fuglu.org','testdata/helloworld.eml')
        suspect.tags['testtag']='testvalue'
        
        headermatches= self.candidate.getArgs(suspect)
        self.failUnless('Sent to unittest domain!' in headermatches, "To_domain not found in headercheck")
        self.failUnless('Envelope sender is sender@unittests.fuglu.org' in headermatches,"Envelope Sender not matched in header chekc")
        self.failUnless('Mime Version is 1.0' in headermatches,"Standard header Mime Version not found")
        self.failUnless('A tag match' in headermatches,"Tag match did not work")
        
        (match,arg)=self.candidate.matches(suspect)
        self.failUnless(match,'Match should return True')

class ActionCodeTestCase(unittest.TestCase):
    def test_defaultcodes(self):
        """test actioncode<->string conversion"""
        conf=ConfigParser.ConfigParser()
        conf.add_section('spam')
        conf.add_section('virus')
        conf.set('spam', 'defaultlowspamaction', 'REJEcT')
        conf.set('spam','defaulthighspamaction','REjECT')
        conf.set('virus','defaultvirusaction','rejeCt')
        self.assertEqual(string_to_actioncode('defaultlowspamaction', conf),REJECT)
        self.assertEqual(string_to_actioncode('defaulthighspamaction', conf),REJECT)
        self.assertEqual(string_to_actioncode('defaultvirusaction', conf),REJECT)
        self.assertEqual(string_to_actioncode('bounce'), BOUNCE)
        self.assertEqual(string_to_actioncode('nonexistingstuff'), None)
        self.assertEqual(actioncode_to_string(REJECT),'REJECT')


class TemplateTestcase(unittest.TestCase):
    """Test Templates"""
    def setUp(self):     
        pass
 
    def tearDown(self):
        pass     

    def test_template(self):
        """Test Basic Template function"""

        suspect=Suspect('sender@unittests.fuglu.org','recipient@unittests.fuglu.org','testdata/helloworld.eml')
        suspect.tags['nobounce']=True
        
        reason="a three-headed monkey stole it"
        
        template="""Your message '${subject}' from ${from_address} to ${to_address} could not be delivered because ${reason}"""
        
        result=apply_template(template, suspect, dict(reason=reason))
        expected="""Your message 'Hello world!' from sender@unittests.fuglu.org to recipient@unittests.fuglu.org could not be delivered because a three-headed monkey stole it"""
        self.assertEquals(result,expected),"Got unexpected template result: %s"%result       
        
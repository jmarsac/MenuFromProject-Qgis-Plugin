"""
/***************************************************************************
Name            : menu_from_project plugin
Description          : Build layers shortcut menu based on QGIS project
Date                 :  10/11/2011
copyright            : (C) 2011 by Agence de l'Eau Adour Garonne
email                : xavier.culos@eau-adour-garonne.fr
***************************************************************************/

/***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************/

"""

import os
import re
import webbrowser
import zipfile

from qgis.core import (
    QgsApplication,
    QgsLayerItem,
    QgsMessageLog,
    QgsProject,
    QgsRasterLayer,
    QgsReadWriteContext,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.PyQt import QtXml
from qgis.PyQt.QtCore import (
    QCoreApplication,
    QDir,
    QFile,
    QFileInfo,
    QIODevice,
    Qt,
    QTemporaryDir,
    QTemporaryFile,
    QTranslator,
    QUuid,
)
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import QAction, QMenu, QWidget
from qgis.utils import plugins, showPluginHelp


# project
from .resources_rc import *  # noqa: F4 I001 - Load Qt compiled resources
from .ui.menu_conf_dlg import MenuConfDialog  # noqa: F4 I001


def getFirstChildByTagNameValue(elt, tagName, key, value):
    nodes = elt.elementsByTagName(tagName)
    for node in (nodes.at(i) for i in range(nodes.size())):
        nd = node.namedItem(key)
        if nd and value == nd.firstChild().toText().data():
            # layer founds
            return node

    return None


def getFirstChildByAttrValue(elt, tagName, key, value):
    nodes = elt.elementsByTagName(tagName)
    for node in (nodes.at(i) for i in range(nodes.size())):
        if (
            node.toElement().hasAttribute(key)
            and node.toElement().attribute(key) == value
        ):
            # layer founds
            return node

    return None


def icon_for_geometry_type(geometry_type):
    """Return the icon for a geometry type.

    If not found, it will return the default icon.

    :param geometry_type: The geometry as a string.
    :type geometry_type: basestring

    :return: The icon.
    :rtype: QIcon
    """
    geometry_type = geometry_type.lower()
    if geometry_type == "raster":
        return QgsLayerItem.iconRaster()
    elif geometry_type == "mesh":
        return QgsLayerItem.iconMesh()
    elif geometry_type == "point":
        return QgsLayerItem.iconPoint()
    elif geometry_type == "line":
        return QgsLayerItem.iconLine()
    elif geometry_type == "polygon":
        return QgsLayerItem.iconPolygon()
    elif geometry_type == "no geometry":
        return QgsLayerItem.iconTable()
    else:
        return QgsLayerItem.iconDefault()


def getMapLayersDict(domdoc):
    r = {}
    nodes = domdoc.documentElement().elementsByTagName("maplayer")
    for node in (nodes.at(i) for i in range(nodes.size())):
        nd = node.namedItem("id")
        if nd:
            r[nd.firstChild().toText().data()] = node

    return r


def isAbsolute(doc):
    """Return true if the given XML document is using absolute path.

    :param doc: The QGIS project as XML document.
    :type doc: QDomDocument
    """
    absolute = False
    try:
        props = doc.elementsByTagName("properties")
        if props.count() == 1:
            node = props.at(0)
            pathNode = node.namedItem("Paths")
            absNode = pathNode.namedItem("Absolute")
            absolute = "true" == absNode.firstChild().toText().data()
    except Exception:
        pass

    return absolute


def project_title(doc):
    """Return the project title defined in the XML document.

    :param doc: The QGIS project as XML document. Default to None.
    :type doc: QDomDocument

    :return: The title or None.
    :rtype: basestring
    """
    tags = doc.elementsByTagName("qgis")
    if tags.count():
        node = tags.at(0)
        title_node = node.namedItem("title")
        return title_node.firstChild().toText().data()

    return None


def project_trusted(doc):
    """Return if the project is trusted.

    :param doc: The QGIS project as XML document. Default to None.
    :type doc: QDomDocument

    :return: True of False.
    :rtype: bool
    """
    tags = doc.elementsByTagName("qgis")
    if tags.count():
        node = tags.at(0)
        trust_node = node.namedItem("trust")
        return trust_node.toElement().attribute("active") == "1"

    return False


class MenuFromProject:
    def on_initializationCompleted(self):
        # build menu
        self.initMenus()

    def __init__(self, iface):
        self.path = QFileInfo(os.path.realpath(__file__)).path()
        self.iface = iface
        self.toolBar = None
        self.project_registry = QgsApplication.projectStorageRegistry()

        # new multi projects var
        self.projects = []
        self.docs = dict()
        self.menubarActions = []
        self.layerMenubarActions = []
        self.canvas = self.iface.mapCanvas()
        self.optionTooltip = False
        self.optionCreateGroup = False
        self.optionLoadAll = False
        self.read()
        settings = QgsSettings()

        if settings.value("menu_from_project/is_setup_visible") is None:
            # This setting does not exist. We add it by default.
            settings.setValue("menu_from_project/is_setup_visible", True)

        # If we want to hide the dialog setup to users.
        self.is_setup_visible = settings.value(
            "menu_from_project/is_setup_visible", True, bool
        )

        self.action_project_configuration = None
        self.action_menu_help = None

        # default lang
        locale = settings.value("locale/userLocale")
        self.myLocale = locale[0:2]
        # dictionary
        localePath = self.path + "/i18n/" + self.myLocale + ".qm"
        # translator
        if QFileInfo(localePath).exists():
            self.translator = QTranslator()
            self.translator.load(localePath)
            QCoreApplication.installTranslator(self.translator)

    @staticmethod
    def tr(message):
        return QCoreApplication.translate("menu_from_project", message)

    @staticmethod
    def log(message, application="Extensions"):
        QgsMessageLog.logMessage(message, application)

    def store(self):
        """Store the configuration in the QSettings."""
        s = QgsSettings()

        s.beginGroup("menu_from_project")
        try:
            s.setValue("optionTooltip", self.optionTooltip)
            s.setValue("optionCreateGroup", self.optionCreateGroup)
            s.setValue("optionLoadAll", self.optionLoadAll)

            s.beginWriteArray("projects", len(self.projects))
            try:
                for i, project in enumerate(self.projects):
                    s.setArrayIndex(i)
                    s.setValue("file", project["file"])
                    s.setValue("name", project["name"])
                    s.setValue("location", project["location"])
            finally:
                s.endArray()
        finally:
            s.endGroup()

    def read(self):
        """Read the configuration from QSettings."""
        s = QgsSettings()
        try:
            s.beginGroup("menu_from_project")
            try:
                self.optionTooltip = s.value("optionTooltip", True, type=bool)
                self.optionCreateGroup = s.value("optionCreateGroup", False, type=bool)
                self.optionLoadAll = s.value("optionLoadAll", False, type=bool)

                size = s.beginReadArray("projects")
                try:
                    for i in range(size):
                        s.setArrayIndex(i)
                        file = s.value("file", "")
                        name = s.value("name", "")
                        location = s.value("location", "new")
                        if file != "":
                            self.projects.append(
                                {"file": file, "name": name, "location": location}
                            )
                finally:
                    s.endArray()

            finally:
                s.endGroup()

        except Exception:
            pass

    def addToolTip(self, ml, action):
        """Search and add a tooltip to a given action according to a maplayer.

        :param ml: The maplayer as XML definition.
        :type ml: documentElement

        :param action: The action.
        :type action: QAction
        """

        if ml is not None:
            try:
                title = ml.namedItem("title").firstChild().toText().data()
                abstract = ml.namedItem("abstract").firstChild().toText().data()

                if (abstract != "") and (title == ""):
                    action.setToolTip(
                        "<p>{}</p>".format("<br/>".join(abstract.split("\n")))
                    )
                else:
                    if abstract != "" or title != "":
                        action.setToolTip(
                            "<b>{}</b><br/>{}".format(
                                title, "<br/>".join(abstract.split("\n"))
                            )
                        )
                    else:
                        action.setToolTip("")
            except Exception:
                pass

    def addMenuItem(self, uri, filename, node, menu, absolute, mapLayersDict):
        """Add menu to an item."""
        yaLayer = False
        if node is None or node.nodeName() == "":
            return yaLayer

        element = node.toElement()

        # if legendlayer tag
        if node.nodeName() == "layer-tree-layer":
            try:
                name = element.attribute("name")
                layerId = element.attribute("id")
                visible = element.attribute("checked", "") == "Qt::Checked"
                expanded = element.attribute("expanded", "0") == "1"
                action = QAction(name, self.iface.mainWindow())
                embedNd = getFirstChildByAttrValue(
                    element, "property", "key", "embedded"
                )

                # is layer embedded ?
                if embedNd and embedNd.toElement().attribute("value") == "1":
                    # layer is embeded
                    efilename = None
                    eFileNd = getFirstChildByAttrValue(
                        element, "property", "key", "embedded_project"
                    )

                    if eFileNd:
                        # get project file name
                        embeddedFile = eFileNd.toElement().attribute("value")
                        if not absolute and (embeddedFile.find(".") == 0):
                            efilename = QFileInfo(filename).path() + "/" + embeddedFile

                        # if ok
                        if efilename:
                            # add menu item
                            action.triggered.connect(
                                lambda checked, uri=uri, f=efilename, lid=layerId, m=menu, v=visible, x=expanded: self.loadLayer(
                                    uri, f, lid, m, v, x
                                )
                            )

                            menu.addAction(action)
                            yaLayer = True

                            if self.optionTooltip:
                                # search embeded maplayer (for title, abstract)
                                mapLayer = self.getMapLayerDomFromQgs(
                                    efilename, layerId
                                )
                                if mapLayer is not None:
                                    self.addToolTip(mapLayer, action)
                    else:
                        self.log(
                            "Menu from layer: {} not found in project {}".format(
                                layerId, efilename
                            )
                        )

                # layer is not embedded
                else:
                    efilename = filename

                    if self.optionTooltip:
                        self.addToolTip(mapLayersDict[layerId], action)

                    action.triggered.connect(
                        lambda checked, uri=uri, f=filename, lid=layerId, m=menu, v=visible, x=expanded: self.loadLayer(
                            uri, f, lid, m, v, x
                        )
                    )

                    menu.addAction(action)
                    yaLayer = True

                # Add geometry type icon
                try:
                    map_layer = self.getMapLayerDomFromQgs(
                        efilename, layerId
                    ).toElement()
                    geometry_type = map_layer.attribute("geometry")
                    if geometry_type == "":
                        # A TMS has not a geometry attribute.
                        # Let's read the "type"
                        geometry_type = map_layer.attribute("type")

                    action.setIcon(icon_for_geometry_type(geometry_type))
                except Exception:
                    pass

            except Exception as e:
                for m in e.args:
                    self.log(m)

        # / if element.tagName() == "layer-tree-layer":

        # if legendgroup tag
        if node.nodeName() == "layer-tree-group":
            name = element.attribute("name")
            propertiesNode = node.firstChild()
            embedNd = getFirstChildByAttrValue(
                propertiesNode.toElement(), "property", "key", "embedded"
            )

            # is group embedded ?
            if embedNd and embedNd.toElement().attribute("value") == "1":
                # group is embeded
                efilename = None
                eFileNd = getFirstChildByAttrValue(
                    element, "property", "key", "embedded_project"
                )

                if eFileNd:
                    # get project file name
                    embeddedFile = eFileNd.toElement().attribute("value")
                    if not absolute and (embeddedFile.find(".") == 0):
                        efilename = QFileInfo(filename).path() + "/" + embeddedFile

                    # if ok
                    if efilename:
                        # add menu group
                        doc, _ = self.getQgsDoc(efilename)

                        groupNode = getFirstChildByAttrValue(
                            doc.documentElement(), "layer-tree-group", "name", name
                        )

                        # and do recursion
                        r = self.addMenuItem(
                            efilename,
                            efilename,
                            groupNode,
                            menu,
                            absolute,
                            getMapLayersDict(doc),
                        )

                        yaLayer = yaLayer or r

                else:
                    self.log(
                        "Menu from layer: {} not found in project {}".format(
                            layerId, efilename
                        )
                    )

            # group is not embedded
            else:

                if name == "-":
                    menu.addSeparator()

                elif name.startswith("-"):
                    action = QAction(name[1:], self.iface.mainWindow())
                    font = QFont()
                    font.setBold(True)
                    action.setFont(font)
                    menu.addAction(action)

                else:
                    # sub-menu
                    sousmenu = menu.addMenu("&" + element.attribute("name"))
                    sousmenu.menuAction().setToolTip("")
                    sousmenu.setToolTipsVisible(self.optionTooltip)

                    childNode = node.firstChild()

                    #  ! recursion
                    r = self.addMenuItem(
                        uri, filename, childNode, sousmenu, absolute, mapLayersDict
                    )

                    if r and self.optionLoadAll and (len(sousmenu.actions()) > 1):
                        action = QAction(self.tr("Load all"), self.iface.mainWindow())
                        font = QFont()
                        font.setBold(True)
                        action.setFont(font)
                        sousmenu.addAction(action)
                        action.triggered.connect(
                            lambda checked, f=None, w=None, m=sousmenu: self.loadLayer(
                                uri, f, w, m
                            )
                        )

        # / if element.tagName() == "legendgroup":

        nextNode = node.nextSibling()
        if nextNode is not None:
            # ! recursion
            r = self.addMenuItem(uri, filename, nextNode, menu, absolute, mapLayersDict)
            yaLayer = yaLayer or r

        return yaLayer

    def addMenu(self, name, uri, filepath, domdoc, location, previous=None):
        """Add menu to the QGIS interface.

        :param name: The name of the parent menu. It might be an empty string.
        :type name: basestring

        :param filepath: The filepath of the project.
        :type filepath: basestring

        :param domdoc: The QGIS project as XML document.
        :type domdoc: QDomDocument

        :param location: The menu location (new menu, or added in "layer - add layer" sub-menu).
        :type location: string

        :param previous: The previous added menu (for merging eventually)
        :type previous: QMenu
        """
        if not name:
            name = project_title(domdoc)

            if not name:
                try:
                    name = filepath.split("/")[-1]
                    name = name.split(".")[0]
                except IndexError:
                    name = ""

        # main project menu
        if location == "merge":
            projectMenu = previous
            projectMenu.addSeparator()
        else:
            if location == "layer":
                menuBar = self.iface.addLayerMenu()
            if location == "new":
                menuBar = self.iface.editMenu().parentWidget()

            projectMenu = QMenu("&" + name, menuBar)
            projectMenu.setToolTipsVisible(self.optionTooltip)
            projectAction = menuBar.addMenu(projectMenu)

            if location == "layer":
                self.layerMenubarActions.append(projectAction)
            if location == "new":
                self.menubarActions.append(projectAction)

        mapLayersDict = getMapLayersDict(domdoc)

        # build menu on legend schema
        legends = domdoc.elementsByTagName("layer-tree-group")
        if legends.length() > 0:
            node = legends.item(0)
            if node:
                node = node.firstChild()
                self.addMenuItem(
                    uri, filepath, node, projectMenu, isAbsolute(domdoc), mapLayersDict
                )

        return projectMenu

    def getQgsDoc(self, uri):
        """Return the XML document and the path from an URI.

        The URI can be a filepath or stored in database.

        :param uri: The URI to fetch.
        :type uri: basestring

        :return: Tuple with XML XML document and the filepath.
        :rtype: (QDomDocument, basestring)
        """

        if uri in self.docs:
            return self.docs[uri], uri

        doc = QtXml.QDomDocument()
        file = QFile(uri)
        # file on disk
        if (
            file.exists()
            and file.open(QIODevice.ReadOnly | QIODevice.Text)
            and QFileInfo(file).suffix() == "qgs"
        ):
            doc.setContent(file)
            project_path = uri

        elif file.exists() and (QFileInfo(file).suffix() == "qgz"):
            temporary_unzip = QTemporaryDir()
            temporary_unzip.setAutoRemove(False)
            with zipfile.ZipFile(uri, "r") as zip_ref:
                zip_ref.extractall(temporary_unzip.path())

            project_filename = QDir(temporary_unzip.path()).entryList(["*.qgs"])[0]
            project_path = os.path.join(temporary_unzip.path(), project_filename)
            xml = QFile(project_path)
            if xml.open(QIODevice.ReadOnly | QIODevice.Text):
                doc.setContent(xml)

        else:
            # uri PG
            project_storage = self.project_registry.projectStorageFromUri(uri)

            temporary_zip = QTemporaryFile()
            temporary_zip.open()
            zip_project = temporary_zip.fileName()

            project_storage.readProject(uri, temporary_zip, QgsReadWriteContext())

            temporary_unzip = QTemporaryDir()
            temporary_unzip.setAutoRemove(False)
            with zipfile.ZipFile(zip_project, "r") as zip_ref:
                zip_ref.extractall(temporary_unzip.path())

            project_filename = QDir(temporary_unzip.path()).entryList(["*.qgs"])[0]
            project_path = os.path.join(temporary_unzip.path(), project_filename)
            xml = QFile(project_path)
            if xml.open(QIODevice.ReadOnly | QIODevice.Text):
                doc.setContent(xml)

        self.docs[project_path] = doc

        return doc, project_path

    def getMapLayerDomFromQgs(self, fileName, layerId):
        """Return the maplayer node in a project filepath given a maplayer ID.

        :param fileName: The project filepath on the filesystem.
        :type fileName: basestring

        :param layerId: The layer ID to look for in the project.
        :type layerId: basestring

        :return: The XML node of the layer.
        :rtype: QDomNode
        """
        doc, _ = self.getQgsDoc(fileName)
        return getFirstChildByTagNameValue(
            doc.documentElement(), "maplayer", "id", layerId
        )

    def initMenus(self):
        menuBar = self.iface.editMenu().parentWidget()
        for action in self.menubarActions:
            menuBar.removeAction(action)
            del action

        self.menubarActions = []

        menuBar = self.iface.addLayerMenu()
        for action in self.layerMenubarActions:
            menuBar.removeAction(action)
            del action

        self.layerMenubarActions = []

        QgsApplication.setOverrideCursor(Qt.WaitCursor)
        previous = None
        for project in self.projects:
            try:
                project["valid"] = True
                uri = project["file"]
                doc, path = self.getQgsDoc(uri)
                previous = self.addMenu(
                    project["name"], uri, path, doc, project["location"], previous
                )
            except Exception as e:
                project["valid"] = False
                self.log("Menu from layer: Invalid {}".format(uri))
                for m in e.args:
                    self.log(m)

        QgsApplication.restoreOverrideCursor()

    def initGui(self):
        if self.is_setup_visible:
            self.action_project_configuration = QAction(
                self.tr("Projects configuration") + "...", self.iface.mainWindow()
            )

            self.iface.addPluginToMenu(
                self.tr("&Layers menu from project"), self.action_project_configuration
            )
            # Add actions to the toolbar
            self.action_project_configuration.triggered.connect(
                self.open_projects_config
            )

            self.action_menu_help = QAction(
                self.tr("Help") + "...", self.iface.mainWindow()
            )
            self.iface.addPluginToMenu(
                self.tr("&Layers menu from project"), self.action_menu_help
            )
            self.action_menu_help.triggered.connect(
                lambda: showPluginHelp(filename="doc/index")
            )

        self.iface.initializationCompleted.connect(self.on_initializationCompleted)

    def unload(self):
        menuBar = self.iface.editMenu().parentWidget()
        for action in self.menubarActions:
            menuBar.removeAction(action)

        menuBar = self.iface.addLayerMenu()
        for action in self.layerMenubarActions:
            menuBar.removeAction(action)

        if self.is_setup_visible:
            self.iface.removePluginMenu(
                self.tr("&Layers menu from project"), self.action_project_configuration
            )
            self.iface.removePluginMenu(
                self.tr("&Layers menu from project"), self.action_menu_help
            )
            self.action_project_configuration.triggered.disconnect(
                self.open_projects_config
            )

        self.store()

    def open_projects_config(self):
        dlg = MenuConfDialog(self.iface.mainWindow(), self)
        dlg.setModal(True)

        dlg.show()
        result = dlg.exec_()
        del dlg

        if result != 0:
            self.initMenus()

    def loadLayer(self, uri, fileName, layerId, menu=None, visible=None, expanded=None):
        """Load the chosen layer(s)

        :param uri: The layer URI (file path or PG URI)
        :type uri: basestring

        :param layerId: The layer ID to look for in the project.
        :type layerId: basestring

        """
        self.canvas.freeze(True)
        self.canvas.setRenderFlag(False)
        group = None
        QgsApplication.setOverrideCursor(Qt.WaitCursor)

        try:
            if (
                isinstance(menu.parentWidget(), (QMenu, QWidget))
                and self.optionCreateGroup
            ):
                groupName = menu.title().replace("&", "")
                group = QgsProject.instance().layerTreeRoot().findGroup(groupName)
                if group is None:
                    group = QgsProject.instance().layerTreeRoot().addGroup(groupName)

            # load all layers
            if fileName is None and layerId is None and self.optionLoadAll:
                for action in menu.actions():
                    if (
                        action.text() != self.tr("Load all")
                        and action.text() != "Load all"
                    ):
                        action.trigger()
            else:
                # read QGIS project
                doc, _ = self.getQgsDoc(fileName)

                # is project in relative path ?
                absolute = isAbsolute(doc)
                trusted = project_trusted(doc)

                node = getFirstChildByTagNameValue(
                    doc.documentElement(), "maplayer", "id", layerId
                )
                node = node.cloneNode()
                if node:
                    idNode = node.namedItem("id")
                    layerType = node.toElement().attribute("type", "vector")
                    # give it a new id (for multiple import)
                    newLayerId = "L%s" % re.sub(
                        "[{}-]", "", QUuid.createUuid().toString()
                    )
                    try:
                        idNode.firstChild().toText().setData(newLayerId)
                    except Exception:
                        pass

                    # if relative path, adapt datasource
                    if not absolute:
                        try:
                            datasourceNode = node.namedItem("datasource")
                            ds = datasourceNode.firstChild().toText().data()
                            providerNode = node.namedItem("provider")
                            provider = providerNode.firstChild().toText().data()

                            if provider in ["ogr", "gdal"] and (ds.find(".") == 0):
                                projectpath = QFileInfo(uri).path()
                                newlayerpath = projectpath + "/" + ds
                                datasourceNode.firstChild().toText().setData(
                                    newlayerpath
                                )
                        except Exception:
                            pass

                    # read modified layer node
                    if self.optionCreateGroup and group is not None:
                        """# sol 1 bug : layer incomplete
                        # because of API strange behaviour, we clone the layer...
                        theLayer = QgsProject.instance().mapLayer(newLayerId)
                        cloneLayer = theLayer.clone()
                        # removing the first
                        QgsProject.instance().removeMapLayer(newLayerId)
                        # adding the clone...
                        treeNode = group.addLayer(cloneLayer)
                        treeNode.setExpanded(expanded)
                        treeNode.setItemVisibilityChecked(visible)"""

                        # solution 2, ok !
                        if layerType == "raster":
                            theLayer = QgsRasterLayer()
                        else:
                            theLayer = QgsVectorLayer()
                            theLayer.setReadExtentFromXml(trusted)

                        theLayer.readLayerXml(node.toElement(), QgsReadWriteContext())

                        # Special process if the plugin "DB Style Manager" is installed
                        flag = "use_db_style_manager_in_custom_menu" in os.environ
                        if flag and "db-style-manager" in plugins:
                            try:
                                plugins["db-style-manager"].load_style_from_database(
                                    theLayer
                                )
                            except Exception:
                                self.log("DB-Style-Manager failed to load the style.")

                        # needed
                        QgsProject.instance().addMapLayer(theLayer, False)
                        # add to group
                        treeNode = group.addLayer(theLayer)
                        treeNode.setExpanded(expanded)
                        treeNode.setItemVisibilityChecked(visible)
                    else:
                        # create layer
                        QgsProject.instance().readLayer(node)

                else:
                    self.log("{} not found".format(layerId))

        except Exception as e:
            # fixme fileName is not defined
            # self.log(
            #     'Menu from layer: Invalid ' + (fileName if fileName is not None else ""))
            for m in e.args:
                self.log(m)

        self.canvas.freeze(False)
        self.canvas.setRenderFlag(True)
        self.canvas.refresh()
        QgsApplication.restoreOverrideCursor()
